# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Carbon spectrum processor: line fitting, solvent exclusion, resonance merging.

Todo 43 / gap G10.  The Bruker chain in :mod:`acp.nmr.spectra` is a base
pipeline (local-maximum picking + midpoint integration); the DP4-AI style
carbon processing this module adds is responsible for the *analysis* layer on
top of a single :class:`~acp.nmr.models.ProcessedSpectrum`:

1. **Line-shape fitting** — the trace is fitted with
   ``scipy.optimize.least_squares`` around each picked line (windows that
   overlap are fitted jointly), using a documented shape model:

   * ``"lorentzian"`` (default) — carbon relaxation is exponential, so the
     absorption line is Lorentzian in the absence of exchange;
   * ``"gaussian"`` / ``"pseudo-voigt"`` (``eta`` mix) selectable via
     :class:`CarbonOptions`.

   Fitted curves and their ppm regions are retained
   (:class:`FittedCurve`) so a human can adjust a region and rerun only the
   analysis.  Without a dense trace (``trace=None``) no fit is claimed: the
   parameters stay the pick-list values and every line carries the explicit
   ``unverified_fit`` / ``trace_unavailable`` markers (never a fabricated
   residual).

2. **Solvent peak exclusion** — residual solvent peaks (CDCl3 triplet at
   77.16 ppm, DMSO-d6 septet at 39.52 ppm, …) are matched against a
   configurable ppm window plus expected multiplicity inside
   :data:`_SOLVENT_DEFAULT_WINDOWS`.  Every window is reported as a
   :class:`SolventAssessment` (``excluded`` / ``ambiguous`` / ``not_detected``
   with a closed reason): a solvent line is never dropped without a record,
   and a partial pattern stays visible as an uncertainty flag instead of
   being silently removed.

3. **Resonance merging** — fitted lines are merged into resonance candidates
   (:class:`Resonance`) by a multiplet-pattern rule (constant spacing, similar
   width/height) or by unresolved overlap (centres closer than ~one FWHM);
   each resonance records its ``merge_basis`` and propagates the member
   lines' uncertainty flags (``low_snr``, ``overlap``, ``unverified_fit``).

**Single-experiment only**: the processor accepts exactly one
:class:`ProcessedSpectrum`; selecting/combining several same-nucleus
experiments is upstream (todo 45) and peaks are never concatenated blindly.

**Registration (todo 45 handoff)**: ``NUCLEUS``/``PROCESSOR_ID`` plus
:func:`get_processor` / :func:`processor_descriptor` expose the nucleus
descriptor and the callable entry point; this module deliberately does *not*
edit ``acp.nmr.__init__`` or ``spectra.py`` — the per-nucleus registry is
wired by todo 45.

**Evaluation**: :func:`compare_resonances_to_annotations` scores produced
resonances against manual annotations (position tolerance plus optional
multiplicity/intensity criteria) and returns precision/recall/F1 and
uncertainty counts; the synthetic fixture in
``tests/test_acp_nmr_carbon_processor.py`` is the binding layer until a real
annotated Bruker carbon dataset ships (the real-data layer is explicitly
``NOT_VERIFIED`` while none exists).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from acp.nmr.models import ProcessedSpectrum, normalize_symbol

logger = logging.getLogger(__name__)

# --- registration descriptor (todo 45 wires the per-nucleus registry) ------

NUCLEUS = "C"
NUCLEUS_LABEL = "13C"
PROCESSOR_ID = "carbon-processor-v1"

#: Documented line-shape model vocabulary.
LINE_SHAPES: tuple[str, ...] = ("lorentzian", "gaussian", "pseudo-voigt")

#: Closed uncertainty-flag vocabulary (line / resonance / result records).
CARBON_FLAGS: tuple[str, ...] = (
    "trace_unavailable",
    "fit_failed",
    "poor_fit",
    "unverified_fit",
    "frequency_unavailable",
    "width_estimated",
    "intensity_estimated",
    "low_snr",
    "overlap",
    "solvent_excluded",
    "solvent_ambiguous",
    "solvent_unknown",
    "no_resonances",
)

#: Why a set of lines forms one resonance candidate (closed vocabulary).
MERGE_BASES: tuple[str, ...] = ("single", "unresolved_overlap", "multiplet_pattern")

#: Solvent-window verdict vocabulary (closed).
SOLVENT_ASSESSMENT_STATUSES: tuple[str, ...] = ("not_detected", "excluded", "ambiguous")
SOLVENT_ASSESSMENT_REASONS: tuple[str, ...] = (
    "no_lines_in_window",
    "solvent_pattern_match",
    "solvent_pattern_mismatch",
    "solvent_intensity_dominant",
    "intensity_below_threshold",
)

#: Why a fitted line is excluded from resonance candidates (closed).
EXCLUSION_REASONS: tuple[str, ...] = ("solvent",)

#: Fit-status vocabulary (closed).
FIT_STATUSES: tuple[str, ...] = ("fitted", "trace_unavailable", "fit_failed")

_DEFAULT_LINE_WIDTH_HZ = 1.0
_CURVE_POINTS = 256


class CarbonProcessingError(ValueError):
    """Typed error for malformed carbon-processing input."""


# ---------------------------------------------------------------------------
# coercion helpers (strict from_dict parsing, models.py style)
# ---------------------------------------------------------------------------


def _require_fields(payload: Mapping[str, object], cls: type) -> None:
    """Reject a payload missing any declared dataclass field."""
    missing = [name for name in cls.__dataclass_fields__ if name not in payload]
    if missing:
        raise ValueError(f"{cls.__name__}.from_dict: missing required field(s) {missing}")


def _coerce_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    return float(value)


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    return _coerce_float(value, field_name)


def _coerce_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {value!r}")
    return int(value)


def _optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _coerce_int(value, field_name)


def _coerce_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {value!r}")
    return value


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _coerce_str(value, field_name)


def _coerce_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list/tuple, got {type(value).__name__}")
    return tuple(_coerce_str(item, f"{field_name}[]") for item in value)


def _coerce_int_tuple(value: object, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list/tuple, got {type(value).__name__}")
    return tuple(_coerce_int(item, f"{field_name}[]") for item in value)


def _coerce_float_tuple(value: object, field_name: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list/tuple, got {type(value).__name__}")
    return tuple(_coerce_float(item, f"{field_name}[]") for item in value)


def _coerce_region(value: object, field_name: str) -> tuple[float, float] | None:
    if value is None:
        return None
    pair = _coerce_float_tuple(value, field_name)
    if len(pair) != 2:
        raise ValueError(f"{field_name} must contain exactly two ppm bounds, got {pair!r}")
    return (pair[0], pair[1])


def _canonical_flags(flags: object) -> tuple[str, ...]:
    """Validate uncertainty flags against the closed vocabulary; canonicalize order."""
    if not isinstance(flags, (list, tuple)):
        raise ValueError(f"flags must be a list/tuple, got {type(flags).__name__}")
    unknown = [flag for flag in flags if not isinstance(flag, str) or flag not in CARBON_FLAGS]
    if unknown:
        raise ValueError(f"unknown carbon flag(s) {unknown!r}; expected a subset of {CARBON_FLAGS}")
    present = set(flags)
    return tuple(flag for flag in CARBON_FLAGS if flag in present)


def _validate_shape(shape: str, field_name: str = "shape") -> str:
    if shape not in LINE_SHAPES:
        raise ValueError(f"{field_name} must be one of {LINE_SHAPES}, got {shape!r}")
    return shape


# ---------------------------------------------------------------------------
# line shapes (documented model; analytic areas)
# ---------------------------------------------------------------------------


def _resolve_eta(eta: float | None) -> float:
    value = 0.5 if eta is None else float(eta)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"pseudo-Voigt eta must be within [0, 1], got {value!r}")
    return value


def evaluate_line_shape(
    ppm: object,
    center: float,
    height: float,
    fwhm: float,
    shape: str,
    eta: float | None = None,
) -> np.ndarray:
    """Evaluate a unit-height line shape on *ppm* (FWHM in ppm).

    Lorentzian: ``h / (1 + (2(x-x0)/w)^2)``; Gaussian:
    ``h * exp(-4 ln2 ((x-x0)/w)^2)``; pseudo-Voigt is the ``eta`` linear mix.
    """
    _validate_shape(shape)
    if not math.isfinite(fwhm) or fwhm <= 0.0:
        raise ValueError(f"fwhm must be a positive finite number, got {fwhm!r}")
    x = np.asarray(ppm, dtype=np.float64)
    scaled = (x - center) / (fwhm / 2.0)
    lorentzian = 1.0 / (1.0 + scaled * scaled)
    if shape == "lorentzian":
        return height * lorentzian
    gaussian = np.exp(-4.0 * math.log(2.0) * np.square((x - center) / fwhm))
    if shape == "gaussian":
        return height * gaussian
    weight = _resolve_eta(eta)
    return height * (weight * lorentzian + (1.0 - weight) * gaussian)


def line_shape_area(
    height: float,
    fwhm: float,
    shape: str,
    eta: float | None = None,
) -> float:
    """Analytic area under the line shape (FWHM in ppm)."""
    _validate_shape(shape)
    if not math.isfinite(fwhm) or fwhm <= 0.0:
        raise ValueError(f"fwhm must be a positive finite number, got {fwhm!r}")
    lorentzian = 0.5 * math.pi * height * fwhm
    if shape == "lorentzian":
        return lorentzian
    gaussian = height * fwhm * math.sqrt(math.pi / (4.0 * math.log(2.0)))
    if shape == "gaussian":
        return gaussian
    weight = _resolve_eta(eta)
    return weight * lorentzian + (1.0 - weight) * gaussian


# ---------------------------------------------------------------------------
# options + solvent windows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SolventWindow:
    """One configurable solvent-exclusion window.

    Attributes:
        solvent: Solvent label this window belongs to (case-insensitive).
        center_ppm: Window centre in ppm (e.g. 77.16 for residual CDCl3).
        window_ppm: Half-width of the window in ppm.
        multiplicity: Expected line count of the solvent pattern (3 for the
            CDCl3 triplet); ``None`` = no pattern requirement.
        min_intensity_fraction: Minimum share of the total observed intensity
            the cluster must carry to be excluded (guards against matching a
            trace-level coincidence).
    """

    solvent: str
    center_ppm: float
    window_ppm: float = 1.0
    multiplicity: int | None = None
    min_intensity_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.window_ppm) or self.window_ppm <= 0.0:
            raise ValueError(f"SolventWindow.window_ppm must be > 0, got {self.window_ppm!r}")
        if self.multiplicity is not None and self.multiplicity < 1:
            raise ValueError(
                f"SolventWindow.multiplicity must be >= 1 or None, got {self.multiplicity!r}"
            )
        if not 0.0 <= self.min_intensity_fraction <= 1.0:
            raise ValueError(
                "SolventWindow.min_intensity_fraction must be within [0, 1], got "
                f"{self.min_intensity_fraction!r}"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "solvent": self.solvent,
            "center_ppm": self.center_ppm,
            "window_ppm": self.window_ppm,
            "multiplicity": self.multiplicity,
            "min_intensity_fraction": self.min_intensity_fraction,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> SolventWindow:
        _require_fields(payload, cls)
        return cls(
            solvent=_coerce_str(payload["solvent"], "SolventWindow.solvent"),
            center_ppm=_coerce_float(payload["center_ppm"], "SolventWindow.center_ppm"),
            window_ppm=_coerce_float(payload["window_ppm"], "SolventWindow.window_ppm"),
            multiplicity=_optional_int(payload["multiplicity"], "SolventWindow.multiplicity"),
            min_intensity_fraction=_coerce_float(
                payload["min_intensity_fraction"], "SolventWindow.min_intensity_fraction"
            ),
        )


#: Residual solvent peak windows (ppm, relative to TMS) and their line counts.
#: Carbon values are the standard residual solvent shifts; a dataset may
#: override them via ``CarbonOptions.solvent_windows``.
_SOLVENT_DEFAULT_WINDOWS: dict[str, tuple[SolventWindow, ...]] = {
    "cdcl3": (SolventWindow("CDCl3", 77.16, 1.2, 3, 0.02),),
    "dmso": (SolventWindow("DMSO-d6", 39.52, 1.5, 7, 0.02),),
    "cd3od": (SolventWindow("CD3OD", 49.00, 1.5, 7, 0.02),),
    "c6d6": (SolventWindow("C6D6", 128.06, 1.2, 3, 0.02),),
    "acetone": (
        SolventWindow("acetone-d6", 29.84, 1.0, 7, 0.02),
        SolventWindow("acetone-d6", 206.26, 1.0, None, 0.02),
    ),
}
#: Solvents known to leave no (or an unmapped) carbon residual peak.
_SOLVENT_KNOWN_NO_WINDOW = frozenset({"d2o", "water", "h2o", "none"})


def resolve_solvent_windows(
    solvent: str | None,
    options: CarbonOptions | None = None,
) -> tuple[SolventWindow, ...]:
    """Resolve the exclusion windows for *solvent*.

    Explicit ``options.solvent_windows`` matching the solvent name override
    the built-in table; otherwise the built-in defaults apply for a known
    solvent.  Unknown solvents return an empty tuple (no window is invented).
    """
    opts = options if options is not None else CarbonOptions()
    key = (solvent or "").strip().lower()
    explicit = tuple(
        window for window in opts.solvent_windows if window.solvent.strip().lower() == key
    )
    if explicit:
        return explicit
    if opts.use_solvent_defaults:
        return _SOLVENT_DEFAULT_WINDOWS.get(key, ())
    return ()


def _solvent_known(solvent: str | None, options: CarbonOptions) -> bool:
    key = (solvent or "").strip().lower()
    if not key:
        return False
    if any(window.solvent.strip().lower() == key for window in options.solvent_windows):
        return True
    if not options.use_solvent_defaults:
        return False
    return key in _SOLVENT_DEFAULT_WINDOWS or key in _SOLVENT_KNOWN_NO_WINDOW


@dataclass(frozen=True)
class CarbonOptions:
    """Carbon-processing options (frozen provenance record, JSON-safe).

    Defaults: Lorentzian fitting, ``min_snr=10``, pattern merging capped at 6
    lines / 6 ppm, built-in solvent windows enabled.
    """

    line_shape: str = "lorentzian"
    pseudo_voigt_eta: float = 0.5
    min_snr: float = 10.0
    fit_window_width_factor: float = 4.0
    min_fit_window_ppm: float = 0.05
    max_fit_window_ppm: float = 4.0
    min_fit_points: int = 8
    poor_fit_factor: float = 5.0
    overlap_width_factor: float = 1.0
    max_splitting_ppm: float = 3.2
    spacing_tolerance_ppm: float = 0.25
    width_ratio_tolerance: float = 3.0
    height_ratio_tolerance: float = 3.5
    min_relative_intensity: float = 0.03
    max_multiplet_lines: int = 6
    max_multiplet_span_ppm: float = 6.0
    min_linewidth_hz: float = 0.1
    default_carbon_mhz: float = 100.0
    use_solvent_defaults: bool = True
    solvent_windows: tuple[SolventWindow, ...] = ()

    def __post_init__(self) -> None:
        _validate_shape(self.line_shape, "CarbonOptions.line_shape")
        if not 0.0 <= self.pseudo_voigt_eta <= 1.0:
            raise ValueError(
                f"CarbonOptions.pseudo_voigt_eta must be within [0, 1], "
                f"got {self.pseudo_voigt_eta!r}"
            )
        if self.min_snr < 0.0:
            raise ValueError(f"CarbonOptions.min_snr must be >= 0, got {self.min_snr!r}")
        if self.fit_window_width_factor <= 0.0 or self.max_fit_window_ppm <= 0.0:
            raise ValueError("CarbonOptions fit-window factors must be > 0")
        if self.min_fit_window_ppm <= 0.0:
            raise ValueError("CarbonOptions.min_fit_window_ppm must be > 0")
        if self.min_fit_points < 4:
            raise ValueError("CarbonOptions.min_fit_points must be >= 4")
        if self.poor_fit_factor <= 0.0 or self.overlap_width_factor <= 0.0:
            raise ValueError("CarbonOptions fit/overlap factors must be > 0")
        if self.max_splitting_ppm <= 0.0 or self.spacing_tolerance_ppm <= 0.0:
            raise ValueError("CarbonOptions splitting limits must be > 0")
        if self.width_ratio_tolerance < 1.0 or self.height_ratio_tolerance < 1.0:
            raise ValueError("CarbonOptions ratio tolerances must be >= 1")
        if not 0.0 < self.min_relative_intensity < 1.0:
            raise ValueError("CarbonOptions.min_relative_intensity must be within (0, 1)")
        if self.max_multiplet_lines < 2 or self.max_multiplet_span_ppm <= 0.0:
            raise ValueError("CarbonOptions multiplet caps must be positive")
        if self.min_linewidth_hz <= 0.0 or self.default_carbon_mhz <= 0.0:
            raise ValueError("CarbonOptions frequency/linewidth defaults must be > 0")
        object.__setattr__(
            self,
            "solvent_windows",
            tuple(
                window if isinstance(window, SolventWindow) else SolventWindow.from_dict(window)
                for window in self.solvent_windows
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "line_shape": self.line_shape,
            "pseudo_voigt_eta": self.pseudo_voigt_eta,
            "min_snr": self.min_snr,
            "fit_window_width_factor": self.fit_window_width_factor,
            "min_fit_window_ppm": self.min_fit_window_ppm,
            "max_fit_window_ppm": self.max_fit_window_ppm,
            "min_fit_points": self.min_fit_points,
            "poor_fit_factor": self.poor_fit_factor,
            "overlap_width_factor": self.overlap_width_factor,
            "max_splitting_ppm": self.max_splitting_ppm,
            "spacing_tolerance_ppm": self.spacing_tolerance_ppm,
            "width_ratio_tolerance": self.width_ratio_tolerance,
            "height_ratio_tolerance": self.height_ratio_tolerance,
            "min_relative_intensity": self.min_relative_intensity,
            "max_multiplet_lines": self.max_multiplet_lines,
            "max_multiplet_span_ppm": self.max_multiplet_span_ppm,
            "min_linewidth_hz": self.min_linewidth_hz,
            "default_carbon_mhz": self.default_carbon_mhz,
            "use_solvent_defaults": self.use_solvent_defaults,
            "solvent_windows": [window.to_dict() for window in self.solvent_windows],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CarbonOptions:
        _require_fields(payload, cls)
        raw_windows = payload["solvent_windows"]
        if not isinstance(raw_windows, (list, tuple)):
            raise ValueError("CarbonOptions.solvent_windows must be a list")
        return cls(
            line_shape=_coerce_str(payload["line_shape"], "CarbonOptions.line_shape"),
            pseudo_voigt_eta=_coerce_float(
                payload["pseudo_voigt_eta"], "CarbonOptions.pseudo_voigt_eta"
            ),
            min_snr=_coerce_float(payload["min_snr"], "CarbonOptions.min_snr"),
            fit_window_width_factor=_coerce_float(
                payload["fit_window_width_factor"], "CarbonOptions.fit_window_width_factor"
            ),
            min_fit_window_ppm=_coerce_float(
                payload["min_fit_window_ppm"], "CarbonOptions.min_fit_window_ppm"
            ),
            max_fit_window_ppm=_coerce_float(
                payload["max_fit_window_ppm"], "CarbonOptions.max_fit_window_ppm"
            ),
            min_fit_points=_coerce_int(payload["min_fit_points"], "CarbonOptions.min_fit_points"),
            poor_fit_factor=_coerce_float(
                payload["poor_fit_factor"], "CarbonOptions.poor_fit_factor"
            ),
            overlap_width_factor=_coerce_float(
                payload["overlap_width_factor"], "CarbonOptions.overlap_width_factor"
            ),
            max_splitting_ppm=_coerce_float(
                payload["max_splitting_ppm"], "CarbonOptions.max_splitting_ppm"
            ),
            spacing_tolerance_ppm=_coerce_float(
                payload["spacing_tolerance_ppm"], "CarbonOptions.spacing_tolerance_ppm"
            ),
            width_ratio_tolerance=_coerce_float(
                payload["width_ratio_tolerance"], "CarbonOptions.width_ratio_tolerance"
            ),
            height_ratio_tolerance=_coerce_float(
                payload["height_ratio_tolerance"], "CarbonOptions.height_ratio_tolerance"
            ),
            min_relative_intensity=_coerce_float(
                payload["min_relative_intensity"], "CarbonOptions.min_relative_intensity"
            ),
            max_multiplet_lines=_coerce_int(
                payload["max_multiplet_lines"], "CarbonOptions.max_multiplet_lines"
            ),
            max_multiplet_span_ppm=_coerce_float(
                payload["max_multiplet_span_ppm"], "CarbonOptions.max_multiplet_span_ppm"
            ),
            min_linewidth_hz=_coerce_float(
                payload["min_linewidth_hz"], "CarbonOptions.min_linewidth_hz"
            ),
            default_carbon_mhz=_coerce_float(
                payload["default_carbon_mhz"], "CarbonOptions.default_carbon_mhz"
            ),
            use_solvent_defaults=bool(payload["use_solvent_defaults"]),
            solvent_windows=tuple(
                SolventWindow.from_dict(_as_mapping(window, "CarbonOptions.solvent_windows[]"))
                for window in raw_windows
            ),
        )


def _as_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping, got {type(value).__name__}")
    return value


# ---------------------------------------------------------------------------
# result records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FittedLine:
    """One observed/fitted carbon line (layer-3 record of the analysis).

    Attributes:
        position_ppm: Fitted (or picked, when no trace) line position.
        intensity: Fitted (or picked) peak height.
        width_hz: FWHM in Hz (``None`` when not measurable).
        area: Fitted analytic area, or the pick-list integral when no trace.
        shape: Line shape used (:data:`LINE_SHAPES`).
        eta: Pseudo-Voigt mixing value (``None`` for pure shapes).
        source_index: Index into ``ProcessedSpectrum.lines`` this line was
            seeded from (``None`` for peak-fallback seeds).
        region: ppm region the line was fitted over (``None`` without trace).
        fit_rms: Local fit residual RMS (``None`` when no fit was performed).
        excluded_reason: Why the line is excluded from resonances
            (:data:`EXCLUSION_REASONS`) or ``None``.
        flags: Uncertainty flags (:data:`CARBON_FLAGS`).
    """

    position_ppm: float
    intensity: float
    width_hz: float | None = None
    area: float | None = None
    shape: str = "lorentzian"
    eta: float | None = None
    source_index: int | None = None
    region: tuple[float, float] | None = None
    fit_rms: float | None = None
    excluded_reason: str | None = None
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_shape(self.shape)
        if self.excluded_reason is not None and self.excluded_reason not in EXCLUSION_REASONS:
            raise ValueError(
                f"unknown exclusion reason {self.excluded_reason!r}; expected {EXCLUSION_REASONS}"
            )
        if not math.isfinite(self.position_ppm) or not math.isfinite(self.intensity):
            raise ValueError("FittedLine position/intensity must be finite")
        if self.region is not None:
            low, high = self.region
            if not low < high:
                raise ValueError(f"FittedLine.region must be (low, high), got {self.region!r}")
        object.__setattr__(self, "flags", _canonical_flags(self.flags))

    def to_dict(self) -> dict[str, object]:
        return {
            "position_ppm": self.position_ppm,
            "intensity": self.intensity,
            "width_hz": self.width_hz,
            "area": self.area,
            "shape": self.shape,
            "eta": self.eta,
            "source_index": self.source_index,
            "region": list(self.region) if self.region is not None else None,
            "fit_rms": self.fit_rms,
            "excluded_reason": self.excluded_reason,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> FittedLine:
        _require_fields(payload, cls)
        name = "FittedLine"
        return cls(
            position_ppm=_coerce_float(payload["position_ppm"], f"{name}.position_ppm"),
            intensity=_coerce_float(payload["intensity"], f"{name}.intensity"),
            width_hz=_optional_float(payload["width_hz"], f"{name}.width_hz"),
            area=_optional_float(payload["area"], f"{name}.area"),
            shape=_coerce_str(payload["shape"], f"{name}.shape"),
            eta=_optional_float(payload["eta"], f"{name}.eta"),
            source_index=_optional_int(payload["source_index"], f"{name}.source_index"),
            region=_coerce_region(payload["region"], f"{name}.region"),
            fit_rms=_optional_float(payload["fit_rms"], f"{name}.fit_rms"),
            excluded_reason=_optional_str(payload["excluded_reason"], f"{name}.excluded_reason"),
            flags=_coerce_str_tuple(payload["flags"], f"{name}.flags"),
        )


@dataclass(frozen=True)
class FittedCurve:
    """A retained fitted-curve region for human adjustment.

    ``ppm``/``intensity`` are the sampled fitted curve over ``region_ppm``
    (ascending); ``line_indices`` name the :class:`FittedLine` members in
    ``CarbonProcessResult.fitted_lines``.
    """

    region_ppm: tuple[float, float]
    ppm: tuple[float, ...]
    intensity: tuple[float, ...]
    fit_rms: float | None = None
    line_indices: tuple[int, ...] = ()
    shape: str = "lorentzian"

    def __post_init__(self) -> None:
        _validate_shape(self.shape)
        low, high = self.region_ppm
        if not low < high:
            raise ValueError(f"FittedCurve.region_ppm must be (low, high), got {self.region_ppm!r}")
        if len(self.ppm) != len(self.intensity):
            raise ValueError("FittedCurve.ppm and intensity must have equal length")

    def to_dict(self) -> dict[str, object]:
        return {
            "region_ppm": list(self.region_ppm),
            "ppm": list(self.ppm),
            "intensity": list(self.intensity),
            "fit_rms": self.fit_rms,
            "line_indices": list(self.line_indices),
            "shape": self.shape,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> FittedCurve:
        _require_fields(payload, cls)
        name = "FittedCurve"
        region = _coerce_region(payload["region_ppm"], f"{name}.region_ppm")
        assert region is not None  # required field, validated by _require_fields
        return cls(
            region_ppm=region,
            ppm=_coerce_float_tuple(payload["ppm"], f"{name}.ppm"),
            intensity=_coerce_float_tuple(payload["intensity"], f"{name}.intensity"),
            fit_rms=_optional_float(payload["fit_rms"], f"{name}.fit_rms"),
            line_indices=_coerce_int_tuple(payload["line_indices"], f"{name}.line_indices"),
            shape=_coerce_str(payload["shape"], f"{name}.shape"),
        )


@dataclass(frozen=True)
class SolventAssessment:
    """Recorded decision for ONE solvent-exclusion window (never silent).

    ``status`` is ``excluded`` (lines removed from resonance candidates),
    ``ambiguous`` (pattern/intensity mismatch — lines retained and flagged
    ``solvent_ambiguous``) or ``not_detected`` (no line in the window).
    """

    solvent: str
    center_ppm: float
    window_ppm: float
    status: str
    reason: str
    expected_multiplicity: int | None = None
    line_indices: tuple[int, ...] = ()
    intensity_fraction: float | None = None

    def __post_init__(self) -> None:
        if self.status not in SOLVENT_ASSESSMENT_STATUSES:
            raise ValueError(
                f"unknown solvent assessment status {self.status!r}; "
                f"expected {SOLVENT_ASSESSMENT_STATUSES}"
            )
        if self.reason not in SOLVENT_ASSESSMENT_REASONS:
            raise ValueError(
                f"unknown solvent assessment reason {self.reason!r}; "
                f"expected {SOLVENT_ASSESSMENT_REASONS}"
            )
        object.__setattr__(self, "line_indices", tuple(int(i) for i in self.line_indices))

    def to_dict(self) -> dict[str, object]:
        return {
            "solvent": self.solvent,
            "center_ppm": self.center_ppm,
            "window_ppm": self.window_ppm,
            "status": self.status,
            "reason": self.reason,
            "expected_multiplicity": self.expected_multiplicity,
            "line_indices": list(self.line_indices),
            "intensity_fraction": self.intensity_fraction,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> SolventAssessment:
        _require_fields(payload, cls)
        name = "SolventAssessment"
        return cls(
            solvent=_coerce_str(payload["solvent"], f"{name}.solvent"),
            center_ppm=_coerce_float(payload["center_ppm"], f"{name}.center_ppm"),
            window_ppm=_coerce_float(payload["window_ppm"], f"{name}.window_ppm"),
            status=_coerce_str(payload["status"], f"{name}.status"),
            reason=_coerce_str(payload["reason"], f"{name}.reason"),
            expected_multiplicity=_optional_int(
                payload["expected_multiplicity"], f"{name}.expected_multiplicity"
            ),
            line_indices=_coerce_int_tuple(payload["line_indices"], f"{name}.line_indices"),
            intensity_fraction=_optional_float(
                payload["intensity_fraction"], f"{name}.intensity_fraction"
            ),
        )


@dataclass(frozen=True)
class Resonance:
    """A carbon resonance candidate: one or more fitted lines merged together.

    ``multiplicity`` is the number of component fitted lines (carbon
    splitting lines); ``merge_basis`` records why they were merged.
    """

    shift_ppm: float
    element: str = NUCLEUS
    line_indices: tuple[int, ...] = ()
    multiplicity: int = 1
    intensity: float = 0.0
    area: float | None = None
    width_hz: float | None = None
    merge_basis: str = "single"
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.merge_basis not in MERGE_BASES:
            raise ValueError(f"unknown merge basis {self.merge_basis!r}; expected {MERGE_BASES}")
        if self.multiplicity < 1:
            raise ValueError(f"Resonance.multiplicity must be >= 1, got {self.multiplicity!r}")
        if not math.isfinite(self.shift_ppm):
            raise ValueError("Resonance.shift_ppm must be finite")
        object.__setattr__(self, "element", normalize_symbol(self.element))
        object.__setattr__(self, "line_indices", tuple(int(i) for i in self.line_indices))
        object.__setattr__(self, "flags", _canonical_flags(self.flags))

    def to_dict(self) -> dict[str, object]:
        return {
            "shift_ppm": self.shift_ppm,
            "element": self.element,
            "line_indices": list(self.line_indices),
            "multiplicity": self.multiplicity,
            "intensity": self.intensity,
            "area": self.area,
            "width_hz": self.width_hz,
            "merge_basis": self.merge_basis,
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Resonance:
        _require_fields(payload, cls)
        name = "Resonance"
        return cls(
            shift_ppm=_coerce_float(payload["shift_ppm"], f"{name}.shift_ppm"),
            element=_coerce_str(payload["element"], f"{name}.element"),
            line_indices=_coerce_int_tuple(payload["line_indices"], f"{name}.line_indices"),
            multiplicity=_coerce_int(payload["multiplicity"], f"{name}.multiplicity"),
            intensity=_coerce_float(payload["intensity"], f"{name}.intensity"),
            area=_optional_float(payload["area"], f"{name}.area"),
            width_hz=_optional_float(payload["width_hz"], f"{name}.width_hz"),
            merge_basis=_coerce_str(payload["merge_basis"], f"{name}.merge_basis"),
            flags=_coerce_str_tuple(payload["flags"], f"{name}.flags"),
        )


@dataclass(frozen=True)
class Annotation:
    """A manual resonance annotation for precision/recall scoring."""

    position_ppm: float
    multiplicity: int | None = None
    intensity: float | None = None
    label: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "position_ppm": self.position_ppm,
            "multiplicity": self.multiplicity,
            "intensity": self.intensity,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> Annotation:
        _require_fields(payload, cls)
        name = "Annotation"
        return cls(
            position_ppm=_coerce_float(payload["position_ppm"], f"{name}.position_ppm"),
            multiplicity=_optional_int(payload["multiplicity"], f"{name}.multiplicity"),
            intensity=_optional_float(payload["intensity"], f"{name}.intensity"),
            label=_optional_str(payload["label"], f"{name}.label"),
        )


@dataclass(frozen=True)
class ResonanceMatch:
    """One accepted annotation↔resonance pair."""

    annotation_index: int
    resonance_index: int
    delta_ppm: float

    def to_dict(self) -> dict[str, object]:
        return {
            "annotation_index": self.annotation_index,
            "resonance_index": self.resonance_index,
            "delta_ppm": self.delta_ppm,
        }


@dataclass(frozen=True)
class ResonanceMatchReport:
    """Precision/recall of produced resonances vs manual annotations.

    ``uncertain_resonances`` lists every produced resonance carrying an
    uncertainty flag; ``uncertain_matches`` lists the resonance indices among
    the matched ones (i.e. matched but uncertain).
    """

    tolerance_ppm: float
    require_multiplicity: bool
    require_intensity: bool
    n_annotations: int
    n_resonances: int
    matches: tuple[ResonanceMatch, ...] = ()
    missed_annotations: tuple[int, ...] = ()
    spurious_resonances: tuple[int, ...] = ()
    uncertain_resonances: tuple[int, ...] = ()
    uncertain_matches: tuple[int, ...] = ()
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "tolerance_ppm": self.tolerance_ppm,
            "require_multiplicity": self.require_multiplicity,
            "require_intensity": self.require_intensity,
            "n_annotations": self.n_annotations,
            "n_resonances": self.n_resonances,
            "matches": [match.to_dict() for match in self.matches],
            "missed_annotations": list(self.missed_annotations),
            "spurious_resonances": list(self.spurious_resonances),
            "uncertain_resonances": list(self.uncertain_resonances),
            "uncertain_matches": list(self.uncertain_matches),
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


@dataclass(frozen=True)
class CarbonProcessResult:
    """Complete carbon-processor output for ONE processed spectrum."""

    processor_id: str
    nucleus_label: str
    spectrum_source: str
    solvent: str | None
    fit_status: str
    options: CarbonOptions
    fitted_lines: tuple[FittedLine, ...] = ()
    curves: tuple[FittedCurve, ...] = ()
    solvent_assessments: tuple[SolventAssessment, ...] = ()
    resonances: tuple[Resonance, ...] = ()
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.fit_status not in FIT_STATUSES:
            raise ValueError(f"unknown fit status {self.fit_status!r}; expected {FIT_STATUSES}")
        object.__setattr__(self, "flags", _canonical_flags(self.flags))
        for name in ("fitted_lines", "curves", "solvent_assessments", "resonances"):
            object.__setattr__(self, name, tuple(getattr(self, name)))

    def compare_to_annotations(
        self,
        annotations: Sequence[Annotation | float],
        *,
        tolerance_ppm: float = 0.05,
        require_multiplicity: bool = False,
        require_intensity: bool = False,
        intensity_rtol: float = 0.25,
    ) -> ResonanceMatchReport:
        """Score this result's resonances against manual annotations."""
        return compare_resonances_to_annotations(
            self,
            annotations,
            tolerance_ppm=tolerance_ppm,
            require_multiplicity=require_multiplicity,
            require_intensity=require_intensity,
            intensity_rtol=intensity_rtol,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "processor_id": self.processor_id,
            "nucleus_label": self.nucleus_label,
            "spectrum_source": self.spectrum_source,
            "solvent": self.solvent,
            "fit_status": self.fit_status,
            "options": self.options.to_dict(),
            "fitted_lines": [line.to_dict() for line in self.fitted_lines],
            "curves": [curve.to_dict() for curve in self.curves],
            "solvent_assessments": [
                assessment.to_dict() for assessment in self.solvent_assessments
            ],
            "resonances": [resonance.to_dict() for resonance in self.resonances],
            "flags": list(self.flags),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> CarbonProcessResult:
        _require_fields(payload, cls)
        name = "CarbonProcessResult"

        def _records(key: str, record_cls: type) -> tuple[Any, ...]:
            raw = payload[key]
            if not isinstance(raw, (list, tuple)):
                raise ValueError(f"{name}.{key} must be a list")
            return tuple(record_cls.from_dict(_as_mapping(item, f"{name}.{key}[]")) for item in raw)

        return cls(
            processor_id=_coerce_str(payload["processor_id"], f"{name}.processor_id"),
            nucleus_label=_coerce_str(payload["nucleus_label"], f"{name}.nucleus_label"),
            spectrum_source=_coerce_str(payload["spectrum_source"], f"{name}.spectrum_source"),
            solvent=_optional_str(payload["solvent"], f"{name}.solvent"),
            fit_status=_coerce_str(payload["fit_status"], f"{name}.fit_status"),
            options=CarbonOptions.from_dict(_as_mapping(payload["options"], f"{name}.options")),
            fitted_lines=_records("fitted_lines", FittedLine),
            curves=_records("curves", FittedCurve),
            solvent_assessments=_records("solvent_assessments", SolventAssessment),
            resonances=_records("resonances", Resonance),
            flags=_coerce_str_tuple(payload["flags"], f"{name}.flags"),
        )


# ---------------------------------------------------------------------------
# internal seeds
# ---------------------------------------------------------------------------


@dataclass
class _Seed:
    """Internal mutable seed (observed line before fitting/merging)."""

    position_ppm: float
    intensity: float
    width_hz: float
    integral: float | None
    source_index: int | None
    flags: list[str]


def _collect_seeds(spectrum: ProcessedSpectrum) -> list[_Seed]:
    """Build fitting seeds from the layer-3 lines (peak fallback)."""
    seeds: list[_Seed] = []
    if spectrum.lines:
        for index, line in enumerate(spectrum.lines):
            flags: list[str] = []
            width = line.width_hz
            if width is None or not math.isfinite(width) or width <= 0.0:
                width = None
            if width is None:
                quality_width = spectrum.quality.linewidth_hz if spectrum.quality else None
                if quality_width is not None and math.isfinite(quality_width) and quality_width > 0:
                    width = float(quality_width)
                else:
                    width = _DEFAULT_LINE_WIDTH_HZ
                flags.append("width_estimated")
            seeds.append(
                _Seed(
                    position_ppm=float(line.position_ppm),
                    intensity=float(line.intensity),
                    width_hz=float(width),
                    integral=line.integral,
                    source_index=index,
                    flags=flags,
                )
            )
        return seeds
    quality_width = spectrum.quality.linewidth_hz if spectrum.quality else None
    width = (
        float(quality_width)
        if quality_width is not None and math.isfinite(quality_width) and quality_width > 0
        else _DEFAULT_LINE_WIDTH_HZ
    )
    for index, peak in enumerate(spectrum.peaks):
        seeds.append(
            _Seed(
                position_ppm=float(peak.shift_ppm),
                intensity=1.0,
                width_hz=width,
                integral=None,
                source_index=index,
                flags=["intensity_estimated", "width_estimated"],
            )
        )
    return seeds


# ---------------------------------------------------------------------------
# solvent exclusion
# ---------------------------------------------------------------------------


def _assess_solvent(
    seeds: Sequence[_Seed],
    windows: Sequence[SolventWindow],
) -> tuple[tuple[SolventAssessment, ...], set[int], set[int]]:
    """Evaluate every window; returns (assessments, excluded, ambiguous)."""
    assessments: list[SolventAssessment] = []
    excluded: set[int] = set()
    ambiguous: set[int] = set()
    total_intensity = float(sum(max(seed.intensity, 0.0) for seed in seeds))
    for window in windows:
        indices = tuple(
            index
            for index, seed in enumerate(seeds)
            if abs(seed.position_ppm - window.center_ppm) <= window.window_ppm
        )
        if not indices:
            assessments.append(
                SolventAssessment(
                    solvent=window.solvent,
                    center_ppm=window.center_ppm,
                    window_ppm=window.window_ppm,
                    status="not_detected",
                    reason="no_lines_in_window",
                    expected_multiplicity=window.multiplicity,
                )
            )
            continue
        cluster_intensity = float(sum(max(seeds[i].intensity, 0.0) for i in indices))
        fraction = cluster_intensity / total_intensity if total_intensity > 0 else 0.0
        if window.multiplicity is not None and len(indices) != window.multiplicity:
            status, reason = "ambiguous", "solvent_pattern_mismatch"
            ambiguous.update(indices)
        elif fraction < window.min_intensity_fraction:
            status, reason = "ambiguous", "intensity_below_threshold"
            ambiguous.update(indices)
        elif window.multiplicity is not None:
            status, reason = "excluded", "solvent_pattern_match"
            excluded.update(indices)
        else:
            status, reason = "excluded", "solvent_intensity_dominant"
            excluded.update(indices)
        assessments.append(
            SolventAssessment(
                solvent=window.solvent,
                center_ppm=window.center_ppm,
                window_ppm=window.window_ppm,
                status=status,
                reason=reason,
                expected_multiplicity=window.multiplicity,
                line_indices=indices,
                intensity_fraction=fraction,
            )
        )
    return tuple(assessments), excluded, ambiguous


# ---------------------------------------------------------------------------
# fitting
# ---------------------------------------------------------------------------


def _carbon_frequency_mhz(
    spectrum: ProcessedSpectrum, options: CarbonOptions
) -> tuple[float, tuple[str, ...]]:
    acquisition = spectrum.acquisition
    if (
        acquisition is not None
        and acquisition.frequency_mhz is not None
        and math.isfinite(acquisition.frequency_mhz)
        and acquisition.frequency_mhz > 0
    ):
        return float(acquisition.frequency_mhz), ()
    return float(options.default_carbon_mhz), ("frequency_unavailable",)


def _seed_line(
    seed: _Seed,
    options: CarbonOptions,
    mhz: float,
    noise: float,
    *,
    base_flags: Sequence[str] = (),
    excluded_reason: str | None = None,
    excluded_flag: str | None = None,
) -> FittedLine:
    """Build a line record from the observed seed (no fit performed)."""
    flags = list(seed.flags) + list(base_flags)
    if excluded_flag is not None:
        flags.append(excluded_flag)
    if noise > 0.0 and seed.intensity >= 0.0 and seed.intensity / noise < options.min_snr:
        flags.append("low_snr")
    width_ppm = seed.width_hz / mhz
    eta = options.pseudo_voigt_eta if options.line_shape == "pseudo-voigt" else None
    if seed.integral is not None and math.isfinite(seed.integral):
        area: float | None = float(seed.integral)
    else:
        area = line_shape_area(seed.intensity, width_ppm, options.line_shape, eta)
    return FittedLine(
        position_ppm=seed.position_ppm,
        intensity=seed.intensity,
        width_hz=seed.width_hz,
        area=area,
        shape=options.line_shape,
        eta=eta,
        source_index=seed.source_index,
        region=None,
        fit_rms=None,
        excluded_reason=excluded_reason,
        flags=tuple(flags),
    )


def _fit_group(
    x: np.ndarray,
    y: np.ndarray,
    group: Sequence[_Seed],
    low: float,
    high: float,
    mhz: float,
    options: CarbonOptions,
) -> tuple[list[tuple[float, float, float]], float]:
    """Joint least-squares fit of one window group; returns (params, rms)."""
    from scipy.optimize import least_squares  # noqa: PLC0415

    min_width = max(options.min_linewidth_hz / mhz, 1e-9)
    max_width = max(high - low, min_width)
    y_max = float(np.max(y)) if y.size else 0.0
    height_ceiling = max(3.0 * y_max, 1e-6)
    x0: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    for seed in group:
        width0 = min(max(seed.width_hz / mhz, min_width), max_width)
        x0.extend([seed.position_ppm, max(seed.intensity, 1e-9), width0])
        lower.extend([low, 0.0, min_width])
        upper.extend([high, height_ceiling, max_width])
    shape = options.line_shape
    eta = options.pseudo_voigt_eta

    def residuals(params: np.ndarray) -> np.ndarray:
        model = np.zeros_like(x)
        for index in range(len(group)):
            center, height, width = params[3 * index : 3 * index + 3]
            model = model + evaluate_line_shape(x, center, height, width, shape, eta)
        return model - y

    result = least_squares(residuals, x0, bounds=(lower, upper), method="trf", max_nfev=2000)
    if not np.all(np.isfinite(result.x)):
        raise FloatingPointError("least_squares returned non-finite parameters")
    rms = float(np.sqrt(np.mean(np.square(result.fun))))
    params = [
        (float(result.x[3 * i]), float(result.x[3 * i + 1]), float(result.x[3 * i + 2]))
        for i in range(len(group))
    ]
    return params, rms


def _cluster_windows(
    windows: Sequence[tuple[float, float, int]],
) -> list[tuple[float, float, list[int]]]:
    """Transitively merge overlapping seed windows into fit regions."""
    ordered = sorted(windows, key=lambda window: (window[0], window[1], window[2]))
    clusters: list[list[Any]] = []
    for low, high, index in ordered:
        if clusters and low <= clusters[-1][1]:
            clusters[-1][1] = max(clusters[-1][1], high)
            clusters[-1][2].append(index)
        else:
            clusters.append([low, high, [index]])
    return [(float(low), float(high), list(indices)) for low, high, indices in clusters]


def _model_values(
    ppm: np.ndarray,
    params: Sequence[tuple[float, float, float]],
    options: CarbonOptions,
) -> np.ndarray:
    values = np.zeros_like(ppm)
    for center, height, width in params:
        values = values + evaluate_line_shape(
            ppm, center, height, width, options.line_shape, options.pseudo_voigt_eta
        )
    return values


def _fit_retained(
    seeds: Sequence[_Seed],
    retained: Sequence[int],
    trace: tuple[Sequence[float], Sequence[float]],
    noise: float,
    mhz: float,
    options: CarbonOptions,
) -> tuple[dict[int, FittedLine], tuple[FittedCurve, ...], bool]:
    """Fit all retained seeds; returns (lines by seed index, curves, fitted?)."""
    if len(trace) != 2:
        raise ValueError("trace must be a (ppm, intensity) pair")
    ppm_values = np.asarray(trace[0], dtype=np.float64)
    intensity_values = np.asarray(trace[1], dtype=np.float64)
    if ppm_values.ndim != 1 or intensity_values.ndim != 1:
        raise ValueError("trace ppm/intensity must be one-dimensional")
    if ppm_values.size != intensity_values.size or ppm_values.size < 2:
        raise ValueError("trace ppm/intensity must have equal length >= 2")
    order = np.argsort(ppm_values)
    ppm_sorted = ppm_values[order]
    intensity_sorted = intensity_values[order]

    windows: list[tuple[float, float, int]] = []
    for index in retained:
        seed = seeds[index]
        half_width = min(
            max(
                options.fit_window_width_factor * (seed.width_hz / mhz), options.min_fit_window_ppm
            ),
            options.max_fit_window_ppm,
        )
        windows.append((seed.position_ppm - half_width, seed.position_ppm + half_width, index))

    lines_by_index: dict[int, FittedLine] = {}
    curves: list[FittedCurve] = []
    any_fitted = False
    for low, high, indices in _cluster_windows(windows):
        group = [seeds[index] for index in indices]
        mask = (ppm_sorted >= low) & (ppm_sorted <= high)
        x = ppm_sorted[mask]
        y = intensity_sorted[mask]
        params: list[tuple[float, float, float]] | None = None
        rms: float | None = None
        if x.size >= options.min_fit_points:
            try:
                params, rms = _fit_group(x, y, group, low, high, mhz, options)
            except (ValueError, FloatingPointError, np.linalg.LinAlgError) as exc:
                logger.warning("carbon fit failed for region [%.4f, %.4f]: %s", low, high, exc)
        if params is None or rms is None:
            for index in indices:
                lines_by_index[index] = _seed_line(
                    seeds[index], options, mhz, noise, base_flags=("fit_failed",)
                )
            continue
        any_fitted = True
        shape = options.line_shape
        eta = options.pseudo_voigt_eta if shape == "pseudo-voigt" else None
        for index, (center, height, width) in zip(indices, params):
            seed = seeds[index]
            flags = list(seed.flags)
            if noise > 0.0 and height / noise < options.min_snr:
                flags.append("low_snr")
            if noise > 0.0 and rms > options.poor_fit_factor * noise:
                flags.append("poor_fit")
            lines_by_index[index] = FittedLine(
                position_ppm=center,
                intensity=height,
                width_hz=width * mhz,
                area=line_shape_area(height, width, shape, eta),
                shape=shape,
                eta=eta,
                source_index=seed.source_index,
                region=(low, high),
                fit_rms=rms,
                flags=tuple(flags),
            )
        grid = np.linspace(low, high, _CURVE_POINTS)
        values = _model_values(grid, params, options)
        curves.append(
            FittedCurve(
                region_ppm=(low, high),
                ppm=tuple(float(value) for value in grid),
                intensity=tuple(float(value) for value in values),
                fit_rms=rms,
                line_indices=tuple(indices),
                shape=shape,
            )
        )
    return lines_by_index, tuple(curves), any_fitted


# ---------------------------------------------------------------------------
# merging
# ---------------------------------------------------------------------------


def _width_ppm(line: FittedLine, mhz: float) -> float:
    width_hz = line.width_hz
    if width_hz is None or not math.isfinite(width_hz) or width_hz <= 0.0:
        width_hz = _DEFAULT_LINE_WIDTH_HZ
    return float(width_hz) / mhz


def _should_merge(
    group: Sequence[tuple[int, FittedLine]],
    item: tuple[int, FittedLine],
    widths_ppm: Mapping[int, float],
    options: CarbonOptions,
) -> bool:
    """Merge decision for one new line into the current (left-to-right) group."""
    previous_index, previous = group[-1]
    new_index, new = item
    gap = new.position_ppm - previous.position_ppm
    if gap <= 0.0:
        return False
    mean_width = 0.5 * (widths_ppm[previous_index] + widths_ppm[new_index])
    if gap <= options.overlap_width_factor * mean_width:
        return True  # unresolved overlap (never a pattern claim)
    if gap > options.max_splitting_ppm:
        return False
    width_pair = sorted((widths_ppm[previous_index], widths_ppm[new_index]))
    if width_pair[0] <= 0.0 or width_pair[1] / width_pair[0] > options.width_ratio_tolerance:
        return False
    members = [line for _, line in group] + [new]
    if any(line.intensity <= 0.0 for line in members):
        return False
    group_max = max(line.intensity for line in members)
    if min(line.intensity for line in members) < options.min_relative_intensity * group_max:
        return False
    if group_max / max(min(line.intensity for line in members), 1e-12) > (
        options.height_ratio_tolerance
    ):
        return False
    if len(group) >= 2:
        previous_gap = previous.position_ppm - group[-2][1].position_ppm
        if abs(gap - previous_gap) > options.spacing_tolerance_ppm:
            return False
    else:
        pair_ratio = max(previous.intensity, new.intensity) / max(
            min(previous.intensity, new.intensity), 1e-12
        )
        if pair_ratio > options.height_ratio_tolerance:
            return False
    if len(group) + 1 > options.max_multiplet_lines:
        return False
    span = new.position_ppm - group[0][1].position_ppm
    if span > options.max_multiplet_span_ppm:
        return False
    return True


def _group_to_resonance(
    group: Sequence[tuple[int, FittedLine]],
    widths_ppm: Mapping[int, float],
    mhz: float,
    *,
    basis: str,
) -> Resonance:
    positions = np.array([line.position_ppm for _, line in group], dtype=np.float64)
    heights = np.array([max(line.intensity, 0.0) for _, line in group], dtype=np.float64)
    total = float(np.sum(heights))
    if total > 0.0:
        shift = float(np.sum(positions * heights) / total)
    else:
        shift = float(np.mean(positions))
    width_hz: float | None = None
    if all(line.width_hz is not None for _, line in group) and total > 0.0:
        width_hz = float(
            np.sum([(line.width_hz or 0.0) * line.intensity for _, line in group]) / total
        )
    areas = [line.area for _, line in group]
    area = float(np.sum(areas)) if all(value is not None for value in areas) else None
    flags = [flag for _, line in group for flag in line.flags]
    if basis == "unresolved_overlap":
        flags.append("overlap")
    ordered = sorted((index, line) for index, line in group)
    return Resonance(
        shift_ppm=shift,
        element=NUCLEUS,
        line_indices=tuple(index for index, _ in ordered),
        multiplicity=len(ordered),
        intensity=total,
        area=area,
        width_hz=width_hz,
        merge_basis=basis,
        flags=tuple(flags),
    )


def _merge_fitted(
    lines: Sequence[FittedLine], options: CarbonOptions, mhz: float
) -> tuple[Resonance, ...]:
    """Merge retained fitted lines into resonance candidates (ascending ppm)."""
    indexed = [(index, line) for index, line in enumerate(lines) if line.excluded_reason is None]
    if not indexed:
        return ()
    widths_ppm = {index: _width_ppm(line, mhz) for index, line in indexed}
    ordered = sorted(indexed, key=lambda pair: pair[1].position_ppm)
    groups: list[list[tuple[int, FittedLine]]] = []
    bases: list[str] = []
    for item in ordered:
        if not groups:
            groups.append([item])
            bases.append("single")
            continue
        if _should_merge(groups[-1], item, widths_ppm, options):
            merged_unresolved = False
            previous = groups[-1][-1][1]
            mean_width = 0.5 * (widths_ppm[groups[-1][-1][0]] + widths_ppm[item[0]])
            if (item[1].position_ppm - previous.position_ppm) <= (
                options.overlap_width_factor * mean_width
            ):
                merged_unresolved = True
            groups[-1].append(item)
            if merged_unresolved:
                bases[-1] = "unresolved_overlap"
            elif bases[-1] == "single":
                bases[-1] = "multiplet_pattern"
        else:
            groups.append([item])
            bases.append("single")
    return tuple(
        _group_to_resonance(group, widths_ppm, mhz, basis=basis)
        for group, basis in zip(groups, bases)
    )


# ---------------------------------------------------------------------------
# public entry points
# ---------------------------------------------------------------------------


def process_carbon_spectrum(
    spectrum: ProcessedSpectrum,
    *,
    trace: tuple[Sequence[float], Sequence[float]] | None = None,
    options: CarbonOptions | None = None,
) -> CarbonProcessResult:
    """Process ONE carbon :class:`ProcessedSpectrum` into resonances.

    Args:
        spectrum: The processed carbon spectrum from
            :mod:`acp.nmr.spectra` (``element == "C"``; a phase-failed
            spectrum is refused — the todo 42 gate is respected).
        trace: Optional ``(ppm, intensity)`` dense processed trace for
            genuine line-shape fitting.  The intensity array must be the same
            processed trace whose noise is recorded in ``spectrum.noise``.
            When absent, no fit is claimed: picked parameters are kept and
            every line carries ``unverified_fit`` / the result carries
            ``trace_unavailable``.
        options: :class:`CarbonOptions` (defaults documented there).

    Raises:
        CarbonProcessingError: For a non-carbon spectrum, a sequence of
            spectra (experiment selection is upstream), a non-ProcessedSpectrum
            input, or a spectrum whose processing gate failed.
    """
    if isinstance(spectrum, (list, tuple)):
        raise CarbonProcessingError(
            "process_carbon_spectrum accepts a single ProcessedSpectrum (one experiment); "
            "got a sequence — same-nucleus experiment selection belongs upstream (todo 45) "
            "and peaks are never concatenated blindly"
        )
    if not isinstance(spectrum, ProcessedSpectrum):
        raise CarbonProcessingError(
            f"process_carbon_spectrum expects a ProcessedSpectrum, got {type(spectrum).__name__}"
        )
    if normalize_symbol(spectrum.element) != NUCLEUS:
        raise CarbonProcessingError(
            f"carbon processor received element {spectrum.element!r} "
            f"(nucleus {spectrum.nucleus!r}); only carbon spectra are accepted"
        )
    if not spectrum.formal_usable:
        reasons = list(spectrum.assessment.reasons) if spectrum.assessment is not None else []
        raise CarbonProcessingError(
            "carbon spectrum failed the processing gate "
            f"({', '.join(reasons) or 'unknown'}); it must not feed formal analysis"
        )
    opts = options if options is not None else CarbonOptions()
    if not isinstance(opts, CarbonOptions):
        raise CarbonProcessingError(f"options must be a CarbonOptions, got {type(opts).__name__}")

    mhz, frequency_flags = _carbon_frequency_mhz(spectrum, opts)
    seeds = _collect_seeds(spectrum)
    solvent = spectrum.acquisition.solvent if spectrum.acquisition is not None else None
    windows = resolve_solvent_windows(solvent, opts)
    assessments, excluded_indices, ambiguous_indices = _assess_solvent(seeds, windows)
    for index in ambiguous_indices:
        seeds[index].flags.append("solvent_ambiguous")

    result_flags: list[str] = list(frequency_flags)
    if not _solvent_known(solvent, opts):
        result_flags.append("solvent_unknown")

    retained = [index for index in range(len(seeds)) if index not in excluded_indices]
    lines_by_index: dict[int, FittedLine] = {}
    fit_status = "trace_unavailable"
    curves: tuple[FittedCurve, ...] = ()
    no_trace_flags: tuple[str, ...] = ("unverified_fit",) if trace is None else ()
    if trace is not None:
        fitted_by_index, curves, any_fitted = _fit_retained(
            seeds, retained, trace, spectrum.noise, mhz, opts
        )
        lines_by_index.update(fitted_by_index)
        fit_status = "fitted" if (any_fitted or not retained) else "fit_failed"
    else:
        for index in retained:
            lines_by_index[index] = _seed_line(
                seeds[index], opts, mhz, spectrum.noise, base_flags=no_trace_flags
            )
    for index in sorted(excluded_indices):
        lines_by_index[index] = _seed_line(
            seeds[index],
            opts,
            mhz,
            spectrum.noise,
            base_flags=no_trace_flags,
            excluded_reason="solvent",
            excluded_flag="solvent_excluded",
        )
    fitted_lines = tuple(lines_by_index[index] for index in sorted(lines_by_index))
    resonances = _merge_fitted(fitted_lines, opts, mhz)

    if fit_status == "trace_unavailable":
        result_flags.append("trace_unavailable")
    if not resonances:
        result_flags.append("no_resonances")
    union_flags = list(result_flags)
    for line in fitted_lines:
        union_flags.extend(line.flags)
    for resonance in resonances:
        union_flags.extend(resonance.flags)

    logger.info(
        "carbon processor: %d line(s), %d resonance(s), fit=%s, solvent=%s, flags=%s",
        len(fitted_lines),
        len(resonances),
        fit_status,
        solvent,
        sorted(set(union_flags)),
    )
    return CarbonProcessResult(
        processor_id=PROCESSOR_ID,
        nucleus_label=NUCLEUS_LABEL,
        spectrum_source=spectrum.source_dir,
        solvent=solvent,
        fit_status=fit_status,
        options=opts,
        fitted_lines=fitted_lines,
        curves=curves,
        solvent_assessments=assessments,
        resonances=resonances,
        flags=tuple(union_flags),
    )


# ---------------------------------------------------------------------------
# precision / recall vs manual annotations
# ---------------------------------------------------------------------------


def compare_resonances_to_annotations(
    resonances: CarbonProcessResult | Sequence[Resonance],
    annotations: Sequence[Annotation | float],
    *,
    tolerance_ppm: float = 0.05,
    require_multiplicity: bool = False,
    require_intensity: bool = False,
    intensity_rtol: float = 0.25,
) -> ResonanceMatchReport:
    """Score produced resonances against a manual annotation list.

    Matching is greedy one-to-one by smallest |Δ| within *tolerance_ppm*;
    with *require_multiplicity* the resonance's component-line count must
    equal the annotation multiplicity (when the annotation declares one),
    and with *require_intensity* the intensities must agree within
    *intensity_rtol* (relative to the annotation intensity).

    Returns precision/recall/F1 plus matched/missed/spurious indices and the
    uncertainty counts (``uncertain_resonances`` = every flagged resonance,
    ``uncertain_matches`` = flagged resonances among the matches).
    """
    if not math.isfinite(tolerance_ppm) or tolerance_ppm <= 0.0:
        raise ValueError(f"tolerance_ppm must be a positive finite number, got {tolerance_ppm!r}")
    if not math.isfinite(intensity_rtol) or intensity_rtol < 0.0:
        raise ValueError(f"intensity_rtol must be a finite number >= 0, got {intensity_rtol!r}")
    items = (
        resonances.resonances if isinstance(resonances, CarbonProcessResult) else tuple(resonances)
    )
    anns = tuple(
        item if isinstance(item, Annotation) else Annotation(position_ppm=float(item))
        for item in annotations
    )
    pairs: list[tuple[float, int, int]] = []
    for annotation_index, annotation in enumerate(anns):
        for resonance_index, resonance in enumerate(items):
            delta = abs(resonance.shift_ppm - annotation.position_ppm)
            if delta > tolerance_ppm:
                continue
            if (
                require_multiplicity
                and annotation.multiplicity is not None
                and resonance.multiplicity != annotation.multiplicity
            ):
                continue
            if require_intensity and annotation.intensity is not None:
                allowed = intensity_rtol * max(abs(annotation.intensity), 1e-12)
                if abs(resonance.intensity - annotation.intensity) > allowed:
                    continue
            pairs.append((delta, annotation_index, resonance_index))
    pairs.sort(key=lambda pair: (pair[0], pair[1], pair[2]))
    used_annotations: set[int] = set()
    used_resonances: set[int] = set()
    matches: list[ResonanceMatch] = []
    for delta, annotation_index, resonance_index in pairs:
        if annotation_index in used_annotations or resonance_index in used_resonances:
            continue
        used_annotations.add(annotation_index)
        used_resonances.add(resonance_index)
        matches.append(
            ResonanceMatch(
                annotation_index=annotation_index,
                resonance_index=resonance_index,
                delta_ppm=delta,
            )
        )
    matches.sort(key=lambda match: match.annotation_index)
    missed = tuple(i for i in range(len(anns)) if i not in used_annotations)
    spurious = tuple(i for i in range(len(items)) if i not in used_resonances)
    uncertain = tuple(i for i, resonance in enumerate(items) if resonance.flags)
    uncertain_set = set(uncertain)
    uncertain_matches = tuple(
        sorted(
            {match.resonance_index for match in matches if match.resonance_index in uncertain_set}
        )
    )
    n_resonances = len(items)
    n_annotations = len(anns)
    matched = len(matches)
    precision = matched / n_resonances if n_resonances else 0.0
    recall = matched / n_annotations if n_annotations else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall > 0.0 else 0.0
    return ResonanceMatchReport(
        tolerance_ppm=tolerance_ppm,
        require_multiplicity=require_multiplicity,
        require_intensity=require_intensity,
        n_annotations=n_annotations,
        n_resonances=n_resonances,
        matches=tuple(matches),
        missed_annotations=missed,
        spurious_resonances=spurious,
        uncertain_resonances=uncertain,
        uncertain_matches=uncertain_matches,
        precision=precision,
        recall=recall,
        f1=f1,
    )


# ---------------------------------------------------------------------------
# nucleus registration descriptor (todo 45 handoff)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarbonProcessor:
    """Callable carbon-processor descriptor for the per-nucleus registry."""

    nucleus: str = NUCLEUS
    nucleus_label: str = NUCLEUS_LABEL
    processor_id: str = PROCESSOR_ID

    def accepts(self, spectrum: ProcessedSpectrum) -> bool:
        """Whether this processor handles *spectrum* (carbon element only)."""
        return (
            isinstance(spectrum, ProcessedSpectrum)
            and normalize_symbol(spectrum.element) == self.nucleus
        )

    def process(
        self,
        spectrum: ProcessedSpectrum,
        *,
        trace: tuple[Sequence[float], Sequence[float]] | None = None,
        options: CarbonOptions | None = None,
    ) -> CarbonProcessResult:
        """Process one carbon spectrum (see :func:`process_carbon_spectrum`)."""
        return process_carbon_spectrum(spectrum, trace=trace, options=options)


PROCESSOR = CarbonProcessor()


def get_processor() -> CarbonProcessor:
    """Return the singleton carbon processor (registry handoff for todo 45)."""
    return PROCESSOR


def processor_descriptor() -> dict[str, str]:
    """JSON-safe registration descriptor for the per-nucleus registry."""
    return {
        "nucleus": NUCLEUS,
        "nucleus_label": NUCLEUS_LABEL,
        "processor_id": PROCESSOR_ID,
        "entry_point": "acp.nmr.carbon_processor.process_carbon_spectrum",
    }


__all__ = [
    "CARBON_FLAGS",
    "EXCLUSION_REASONS",
    "FIT_STATUSES",
    "LINE_SHAPES",
    "MERGE_BASES",
    "NUCLEUS",
    "NUCLEUS_LABEL",
    "PROCESSOR",
    "PROCESSOR_ID",
    "SOLVENT_ASSESSMENT_REASONS",
    "SOLVENT_ASSESSMENT_STATUSES",
    "Annotation",
    "CarbonOptions",
    "CarbonProcessingError",
    "CarbonProcessResult",
    "CarbonProcessor",
    "FittedCurve",
    "FittedLine",
    "Resonance",
    "ResonanceMatch",
    "ResonanceMatchReport",
    "SolventAssessment",
    "SolventWindow",
    "compare_resonances_to_annotations",
    "evaluate_line_shape",
    "get_processor",
    "line_shape_area",
    "process_carbon_spectrum",
    "processor_descriptor",
    "resolve_solvent_windows",
]
