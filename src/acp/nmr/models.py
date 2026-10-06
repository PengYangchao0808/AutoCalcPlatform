# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false
"""NMR + DP4/DP5 domain models.

The dataclasses in this module are the in-memory representation that flows
between the stages of the NMR workflow (DevDoc §5): experimental input,
per-conformer shieldings, Boltzmann-averaged candidate shieldings, the
assignment / scaling products, and the final DP4/DP5 probabilities.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# --- element / nucleus helpers -------------------------------------------


_NMR_ACTIVE_ELEMENTS: tuple[str, ...] = ("H", "C", "N", "F", "P")


# ---------------------------------------------------------------------------
# TMS reference table (Goodman DP5 TMSdata, verified 2026-08-07)
# ---------------------------------------------------------------------------


def _load_tms_table() -> dict[tuple[str, str, str], tuple[float, float]]:
    """Load the Goodman TMS reference table at first use.

    Returns ``{(method, basis, solvent): (sigma_13C, sigma_1H)}`` with all
    keys lowercased and whitespace-stripped. Source: ``acp/nmr/models/
    tms_references.txt`` (Goodman-lab/DP5 ``TMSdata``).
    """
    table: dict[tuple[str, str, str], tuple[float, float]] = {}
    try:
        path = Path(__file__).resolve().parent / "models" / "tms_references.txt"
        if not path.exists():
            return table
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            method = parts[0].lower()
            basis = parts[1].lower().replace(" ", "")
            solvent = parts[2].lower()
            sigma_c = float(parts[3])
            sigma_h = float(parts[4])
            table[(method, basis, solvent)] = (sigma_c, sigma_h)
    except Exception as exc:  # pragma: no cover - best-effort load
        logger.warning("Failed to load TMS reference table: %s", exc)
    return table


_TMS_TABLE: dict[tuple[str, str, str], tuple[float, float]] | None = None


def lookup_tms_shieldings(
    method: str,
    basis: str,
    solvent: str | None,
) -> tuple[float | None, float | None]:
    """Return ``(sigma_13C, sigma_1H)`` for the given level, or ``(None, None)``.

    Matches case-insensitively on (method, basis, solvent). Gas phase is
    keyed by ``solvent="none"``; an unknown solvent falls back to gas phase.
    """
    global _TMS_TABLE
    if _TMS_TABLE is None:
        _TMS_TABLE = _load_tms_table()
    if not _TMS_TABLE:
        return None, None
    m = method.strip().lower()
    b = basis.strip().lower().replace(" ", "")
    s = (solvent or "none").strip().lower()
    for key_solvent in (s, "none"):
        pair = _TMS_TABLE.get((m, b, key_solvent))
        if pair is not None:
            return pair[0], pair[1]
    return None, None


def normalize_symbol(symbol: str) -> str:
    """Return a normalized element symbol (Title-case, stripped)."""
    s = symbol.strip()
    if not s:
        return s
    return s[:1].upper() + s[1:].lower()


def nucleus_label(element: str, mass_number: int | None = None) -> str:
    """Return the canonical nucleus label, e.g. ``"13C"`` / ``"1H"``.

    Defaults: H→1H, C→13C, N→15N, F→19F, P→31P.
    """
    sym = normalize_symbol(element)
    defaults = {"H": 1, "C": 13, "N": 15, "F": 19, "P": 31}
    num = mass_number if mass_number is not None else defaults.get(sym, 1)
    return f"{num}{sym}"


def element_of_nucleus(nucleus: str) -> str:
    """Return the element symbol for a nucleus label like ``"13C"``."""
    text = nucleus.strip()
    # strip leading digits
    i = 0
    while i < len(text) and text[i].isdigit():
        i += 1
    return normalize_symbol(text[i:]) if i < len(text) else normalize_symbol(text)


# --- input data ----------------------------------------------------------


ParseIssueCode = Literal[
    "unknown_token",
    "duplicate_label",
    "missing_label",
    "unmatched_peak",
    "unmatched_atom",
    "ambiguous_label",
]

PARSE_ISSUE_CODES: tuple[str, ...] = (
    "unknown_token",
    "duplicate_label",
    "missing_label",
    "unmatched_peak",
    "unmatched_atom",
    "ambiguous_label",
)


@dataclass(frozen=True)
class ParseIssue:
    """One parse-time problem in experimental NMR input (G02/G03).

    Readable via ``str()`` (``[code] detail (token: ...)``) and
    serializable via :meth:`to_dict`.
    """

    code: ParseIssueCode
    detail: str
    token: str = ""

    def __str__(self) -> str:
        base = f"[{self.code}] {self.detail}"
        return f"{base} (token: {self.token!r})" if self.token else base

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail, "token": self.token}


@dataclass(frozen=True)
class ExperimentalPeak:
    """One experimental resonance.

    Attributes:
        shift_ppm: Chemical shift in ppm.
        atom_label: Resolved single assignment (e.g. ``"C1"``). ``None``
            when unassigned or ambiguous — never one pick of a set.
        multiplicity: Integral multiplicity (e.g. 3 for CH3). Defaults to 1.
        element: Element this peak belongs to (``"H"``/``"C"``/...).
        label_candidates: ``None`` when the peak carries no label; a tuple
            of candidate atom labels otherwise (length 1 = explicit, >1 =
            ambiguous input kept as a set).
        index: Position within its element's peak list (parser-assigned
            stable identity for downstream matching).
    """

    shift_ppm: float
    element: str
    atom_label: str | None = None
    multiplicity: int = 1
    label_candidates: tuple[str, ...] | None = None
    index: int | None = None

    @property
    def assigned(self) -> bool:
        """True only when this peak resolves to exactly one atom label."""
        if self.atom_label is None:
            return False
        if self.label_candidates is None:
            return True
        return len(self.label_candidates) == 1 and self.label_candidates[0] == self.atom_label

    @property
    def ambiguous(self) -> bool:
        """True for unassigned-with-candidates (several labels, no resolution)."""
        return self.atom_label is None and bool(self.label_candidates)

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (exact values; candidates as list or ``None``)."""
        return {
            "shift_ppm": self.shift_ppm,
            "element": self.element,
            "atom_label": self.atom_label,
            "multiplicity": self.multiplicity,
            "label_candidates": (
                list(self.label_candidates) if self.label_candidates is not None else None
            ),
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ExperimentalPeak:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        candidates_raw = payload["label_candidates"]
        return cls(
            shift_ppm=_coerce_required_float(payload["shift_ppm"], "ExperimentalPeak.shift_ppm"),
            element=_coerce_required_str(payload["element"], "ExperimentalPeak.element"),
            atom_label=_payload_str(payload, "ExperimentalPeak", "atom_label"),
            multiplicity=_coerce_required_int(
                payload["multiplicity"], "ExperimentalPeak.multiplicity"
            ),
            label_candidates=(
                None
                if candidates_raw is None
                else _coerce_str_tuple(candidates_raw, "ExperimentalPeak.label_candidates")
            ),
            index=_payload_int(payload, "ExperimentalPeak", "index"),
        )


@dataclass
class ExperimentalNmr:
    """Parsed experimental NMR input (DevDoc §6.2).

    Attributes:
        peaks: Peaks grouped by element (``"H"``/``"C"`` → list of peaks).
        equivalence_groups: Equivalence groups (lists of atom labels); each
            group is averaged to a single computed signal before matching.
        omit_atoms: Atom labels excluded from comparison.
        assigned: Legacy whole-spectrum flag — ``True`` only when EVERY
            peak is assigned. Mixed input must not flip it (per-peak
            :attr:`ExperimentalPeak.assigned` and :meth:`assignment_counts`
            are authoritative).
        parse_errors: Issues found while parsing (unknown tokens, missing/
            duplicate labels, unmatched peaks/atoms, ambiguous labels).
    """

    peaks: dict[str, list[ExperimentalPeak]] = field(default_factory=dict)
    equivalence_groups: list[list[str]] = field(default_factory=list)
    omit_atoms: list[str] = field(default_factory=list)
    assigned: bool = False
    parse_errors: list[ParseIssue] = field(default_factory=list)

    def nuclei(self) -> list[str]:
        """Return the sorted element symbols actually present."""
        return sorted(self.peaks.keys())

    def peaks_for(self, element: str) -> list[ExperimentalPeak]:
        """Return the peaks for one element (normalized)."""
        return self.peaks.get(normalize_symbol(element), [])

    def assignment_counts(self) -> dict[str, tuple[int, int]]:
        """Per-element ``(assigned, total)`` peak counts."""
        return {
            element: (sum(1 for p in group if p.assigned), len(group))
            for element, group in self.peaks.items()
        }

    @property
    def assigned_nuclei(self) -> list[str]:
        """Elements with at least one explicitly assigned peak."""
        return sorted(
            element for element, group in self.peaks.items() if any(p.assigned for p in group)
        )


@dataclass(frozen=True)
class NmrConfig:
    """Configuration for a single NMR workflow run.

    Defaults follow DevDoc §6.4 / §8.0: ``mPW1PW91/6-311G(d)`` (Goodman
    reference level), chloroform solvent, 298.15 K Boltzmann temperature,
    placeholder TMS references and the ``goodman-legacy`` error model.
    """

    nuclei: tuple[str, ...] = ("1H", "13C")
    nmr_method: str = "mPW1PW91"
    nmr_basis: str = "6-311G(d)"
    solvent: str | None = "chloroform"
    solvent_model: str = "cpcm"
    tms_shieldings: dict[str, float] = field(
        default_factory=lambda: {
            # Goodman DP5 TMSdata for mPW1PW91/6-311G(d)/chloroform
            "1H": 32.1243166667,
            "13C": 188.452125,
        }
    )
    boltzmann_temp: float = 298.15
    energy_window_kcal: float = 3.0
    max_conformers: int = 10
    error_model: str = "goodman-legacy"
    conformer_preset: str = "censo-light"
    strict_equivalence: bool = False
    #: D-phase (todo 29) shielding checkpoint fingerprint — geometry hash +
    #: method/basis/solvent/solvent_model/nuclei/charge/multiplicity.
    #: ``None`` until the checkpoint lands; placeholder reserved by todo 21.
    protocol_fingerprint: str | None = None

    @property
    def tms_1h(self) -> float | None:
        """Flat provenance view: 1H TMS reference shielding (``None`` unset)."""
        return self.tms_shieldings.get("1H")

    @property
    def tms_13c(self) -> float | None:
        """Flat provenance view: 13C TMS reference shielding (``None`` unset)."""
        return self.tms_shieldings.get("13C")

    def tms_for(self, nucleus: str) -> float | None:
        """Return the TMS reference shielding for a nucleus label."""
        return self.tms_shieldings.get(nucleus)

    def element_nuclei(self, symbols: list[str]) -> list[str]:
        """Return the configured nuclei whose element is present in *symbols*."""
        present = {normalize_symbol(s) for s in symbols}
        return [n for n in self.nuclei if element_of_nucleus(n) in present]

    def to_dict(self) -> dict[str, object]:
        """JSON-safe effective-config record (report schema v2 provenance).

        Fields: nuclei, level (method/basis), solvent + solvent_model,
        TMS references (flat + table), Boltzmann temperature, energy window,
        conformer limits/preset, error model, strict-equivalence flag and
        the D-phase ``protocol_fingerprint`` placeholder.
        """
        return {
            "nuclei": list(self.nuclei),
            "nmr_method": self.nmr_method,
            "nmr_basis": self.nmr_basis,
            "solvent": self.solvent,
            "solvent_model": self.solvent_model,
            "tms_shieldings": dict(self.tms_shieldings),
            "tms_1h": self.tms_1h,
            "tms_13c": self.tms_13c,
            "boltzmann_temp": self.boltzmann_temp,
            "energy_window_kcal": self.energy_window_kcal,
            "max_conformers": self.max_conformers,
            "error_model": self.error_model,
            "conformer_preset": self.conformer_preset,
            "strict_equivalence": self.strict_equivalence,
            "protocol_fingerprint": self.protocol_fingerprint,
        }


# --- four-layer spectrum model (todo 41 / G10) ------------------------------
#
# Layer separation (never conflated):
#   acquisition → AcquisitionSpectrum: what the spectrometer recorded;
#   processed   → ProcessedSpectrum: processed data + ProcessingProvenance
#                 + ProcessingQuality + observed/fitted lines;
#   lines       → SpectralLine: elementary lines of the processed data;
#   resonances  → ResonanceSignal: chemically meaningful assigned signals,
#                 created only through assignment — a raw unmatched peak is
#                 NEVER auto-promoted to a resonance.


def _require_payload_fields(payload: Mapping[str, object], cls: type) -> None:
    """Reject a layer payload missing any declared field (no silent defaults)."""
    missing = [f.name for f in fields(cls) if f.name not in payload]
    if missing:
        raise ValueError(f"{cls.__name__}.from_dict: missing required field(s) {missing}")


def _coerce_optional_float(value: object, field_name: str) -> float | None:
    """Return *value* as float, or ``None``; reject non-numeric payloads."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number or None, got {value!r}")
    return float(value)


def _coerce_optional_int(value: object, field_name: str) -> int | None:
    """Return *value* as int, or ``None``; reject non-integer payloads."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int or None, got {value!r}")
    return int(value)


def _coerce_optional_str(value: object, field_name: str) -> str | None:
    """Return *value* as str, or ``None``; reject non-string payloads."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or None, got {value!r}")
    return value


def _coerce_required_float(value: object, field_name: str) -> float:
    """Return *value* as float; reject booleans and non-numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number, got {value!r}")
    return float(value)


def _coerce_required_int(value: object, field_name: str) -> int:
    """Return *value* as int; reject booleans and non-integers."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an int, got {value!r}")
    return int(value)


def _coerce_required_str(value: object, field_name: str) -> str:
    """Return *value* as str; reject non-strings."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string, got {value!r}")
    return value


def _coerce_str_tuple(value: object, field_name: str) -> tuple[str, ...]:
    """Return *value* as a tuple of strings; reject non-sequences/members."""
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list/tuple, got {type(value).__name__}")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings, got {item!r}")
        out.append(item)
    return tuple(out)


def _coerce_payload_mapping(value: object, field_name: str) -> Mapping[str, object]:
    """Return *value* as a mapping; reject non-mappings."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping, got {type(value).__name__}")
    return value


def _payload_float(payload: Mapping[str, object], cls_name: str, key: str) -> float | None:
    """Read an optional float field from an already-validated payload."""
    return _coerce_optional_float(payload[key], f"{cls_name}.{key}")


def _payload_int(payload: Mapping[str, object], cls_name: str, key: str) -> int | None:
    """Read an optional int field from an already-validated payload."""
    return _coerce_optional_int(payload[key], f"{cls_name}.{key}")


def _payload_str(payload: Mapping[str, object], cls_name: str, key: str) -> str | None:
    """Read an optional str field from an already-validated payload."""
    return _coerce_optional_str(payload[key], f"{cls_name}.{key}")


@dataclass(frozen=True)
class AcquisitionSpectrum:
    """Layer 1 — raw acquisition metadata recorded by the spectrometer.

    Container only: processing decisions (windows, phase, baseline,
    referencing) live in :class:`ProcessingProvenance`, never here.
    ``point_count`` is the number of acquired complex points actually read
    from the raw FID.

    Attributes:
        spectrometer: Instrument label the reader is bound to.
        nucleus: Nucleus label from ``acqus`` (e.g. ``"1H"``).
        frequency_mhz: Observe frequency (``SFO1``/``BF1``) in MHz.
        solvent: Solvent as recorded in ``acqus`` (e.g. ``"CDCl3"``).
        temperature_k: Sample temperature in K (``TE``).
        pulse_program: Pulse program name (``PULPROG``).
        point_count: Acquired complex points in the direct dimension.
        spectral_width_hz: Spectral width in Hz (``SW_h``).
        carrier_ppm: Carrier/offset position in ppm (``O1``/``SFO1``).
        group_delay_points: Raw Bruker group delay (``GRPDLY``, points) —
            digital-filter provenance for the todo 42 compensation audit.
        dspfvs: Bruker DSP firmware version parameter (``DSPFVS``).
        spectrometer_reference: Raw ``SR`` referencing value as recorded
            (units as stored in ``acqus``); ``None`` when absent.
        source_dir: Bruker experiment directory the spectrum came from.
    """

    spectrometer: str
    nucleus: str
    frequency_mhz: float | None = None
    solvent: str | None = None
    temperature_k: float | None = None
    pulse_program: str | None = None
    point_count: int | None = None
    spectral_width_hz: float | None = None
    carrier_ppm: float | None = None
    group_delay_points: float | None = None
    dspfvs: int | None = None
    spectrometer_reference: float | None = None
    source_dir: str = ""

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (every declared field, exact values)."""
        return {
            "spectrometer": self.spectrometer,
            "nucleus": self.nucleus,
            "frequency_mhz": self.frequency_mhz,
            "solvent": self.solvent,
            "temperature_k": self.temperature_k,
            "pulse_program": self.pulse_program,
            "point_count": self.point_count,
            "spectral_width_hz": self.spectral_width_hz,
            "carrier_ppm": self.carrier_ppm,
            "group_delay_points": self.group_delay_points,
            "dspfvs": self.dspfvs,
            "spectrometer_reference": self.spectrometer_reference,
            "source_dir": self.source_dir,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> AcquisitionSpectrum:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "AcquisitionSpectrum"
        return cls(
            spectrometer=_coerce_required_str(payload["spectrometer"], f"{name}.spectrometer"),
            nucleus=_coerce_required_str(payload["nucleus"], f"{name}.nucleus"),
            frequency_mhz=_payload_float(payload, name, "frequency_mhz"),
            solvent=_payload_str(payload, name, "solvent"),
            temperature_k=_payload_float(payload, name, "temperature_k"),
            pulse_program=_payload_str(payload, name, "pulse_program"),
            point_count=_payload_int(payload, name, "point_count"),
            spectral_width_hz=_payload_float(payload, name, "spectral_width_hz"),
            carrier_ppm=_payload_float(payload, name, "carrier_ppm"),
            group_delay_points=_payload_float(payload, name, "group_delay_points"),
            dspfvs=_payload_int(payload, name, "dspfvs"),
            spectrometer_reference=_payload_float(payload, name, "spectrometer_reference"),
            source_dir=_coerce_required_str(payload["source_dir"], f"{name}.source_dir"),
        )


@dataclass(frozen=True)
class ProcessingProvenance:
    """Layer 2a — the processing chain ACTUALLY applied to one spectrum (G10).

    Records apodization / digital-filter window parameters (LB/GB/SB),
    zero-filling, phase (method + PHC0/PHC1 when known — ``None`` when the
    optimizer does not report angles), baseline correction and referencing
    (manual anchor vs plain spectrometer reference). Nothing here is
    assumed: unset numeric parameters stay ``None``.

    Attributes:
        apodization: Window function applied (e.g. ``"exponential"``).
        lb_hz: Exponential line broadening (LB) in Hz.
        gb: Gaussian broadening (GB) parameter.
        sb: Sine-bell shift (SB) parameter.
        zero_fill_points: Final complex point count after zero-filling.
        zero_fill_factor: Final / original point count.
        phase_method: ``"peak_minima"``, ``"acme"``, ``"manual"``,
            ``"unphased"`` (every optimizer failed) or ``"none"``.
        phase_p0_deg: Zero-order phase (TopSpin ``PHC0``/``p0``), degrees.
        phase_p1_deg: First-order phase (TopSpin ``PHC1``/``p1``), degrees.
        baseline_method: Baseline-correction description.
        baseline_window_fraction: Window fraction used by the correction.
        reference_method: ``"spectrometer_sr"`` (acquisition referencing
            trusted), ``"manual_anchor"`` (a picked peak was anchored to a
            requested reference) or ``"none"``.
        reference_ppm: Requested reference position in ppm (``None`` when no
            manual reference was requested).
        applied_shift_ppm: ppm shift actually applied to the picked peaks
            (``None`` when no anchor was applied).
    """

    apodization: str
    phase_method: str
    baseline_method: str
    reference_method: str
    lb_hz: float | None = None
    gb: float | None = None
    sb: float | None = None
    zero_fill_points: int | None = None
    zero_fill_factor: float | None = None
    phase_p0_deg: float | None = None
    phase_p1_deg: float | None = None
    baseline_window_fraction: float | None = None
    reference_ppm: float | None = None
    applied_shift_ppm: float | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (every declared field, exact values)."""
        return {
            "apodization": self.apodization,
            "lb_hz": self.lb_hz,
            "gb": self.gb,
            "sb": self.sb,
            "zero_fill_points": self.zero_fill_points,
            "zero_fill_factor": self.zero_fill_factor,
            "phase_method": self.phase_method,
            "phase_p0_deg": self.phase_p0_deg,
            "phase_p1_deg": self.phase_p1_deg,
            "baseline_method": self.baseline_method,
            "baseline_window_fraction": self.baseline_window_fraction,
            "reference_method": self.reference_method,
            "reference_ppm": self.reference_ppm,
            "applied_shift_ppm": self.applied_shift_ppm,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ProcessingProvenance:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "ProcessingProvenance"
        return cls(
            apodization=_coerce_required_str(payload["apodization"], f"{name}.apodization"),
            lb_hz=_payload_float(payload, name, "lb_hz"),
            gb=_payload_float(payload, name, "gb"),
            sb=_payload_float(payload, name, "sb"),
            zero_fill_points=_payload_int(payload, name, "zero_fill_points"),
            zero_fill_factor=_payload_float(payload, name, "zero_fill_factor"),
            phase_method=_coerce_required_str(payload["phase_method"], f"{name}.phase_method"),
            phase_p0_deg=_payload_float(payload, name, "phase_p0_deg"),
            phase_p1_deg=_payload_float(payload, name, "phase_p1_deg"),
            baseline_method=_coerce_required_str(
                payload["baseline_method"], f"{name}.baseline_method"
            ),
            baseline_window_fraction=_payload_float(payload, name, "baseline_window_fraction"),
            reference_method=_coerce_required_str(
                payload["reference_method"], f"{name}.reference_method"
            ),
            reference_ppm=_payload_float(payload, name, "reference_ppm"),
            applied_shift_ppm=_payload_float(payload, name, "applied_shift_ppm"),
        )


@dataclass(frozen=True)
class ProcessingQuality:
    """Layer 2b — quality metrics of one processed spectrum (G10).

    Each metric is ``None`` when it could not be computed (e.g. no picked
    peak for S/N and linewidth) — never fabricated as 0.
    """

    snr: float | None = None
    linewidth_hz: float | None = None
    baseline_rms: float | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (every declared field, exact values)."""
        return {
            "snr": self.snr,
            "linewidth_hz": self.linewidth_hz,
            "baseline_rms": self.baseline_rms,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ProcessingQuality:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "ProcessingQuality"
        return cls(
            snr=_payload_float(payload, name, "snr"),
            linewidth_hz=_payload_float(payload, name, "linewidth_hz"),
            baseline_rms=_payload_float(payload, name, "baseline_rms"),
        )


@dataclass(frozen=True)
class SpectralLine:
    """Layer 3 — one observed/fitted line of the processed data.

    Processing output only: a line carries no chemical assignment — turning
    lines into signals is an assignment step (:class:`ResonanceSignal`).
    """

    position_ppm: float
    intensity: float
    width_hz: float | None = None
    integral: float | None = None
    index: int | None = None

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (every declared field, exact values)."""
        return {
            "position_ppm": self.position_ppm,
            "intensity": self.intensity,
            "width_hz": self.width_hz,
            "integral": self.integral,
            "index": self.index,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> SpectralLine:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "SpectralLine"
        return cls(
            position_ppm=_coerce_required_float(payload["position_ppm"], f"{name}.position_ppm"),
            intensity=_coerce_required_float(payload["intensity"], f"{name}.intensity"),
            width_hz=_payload_float(payload, name, "width_hz"),
            integral=_payload_float(payload, name, "integral"),
            index=_payload_int(payload, name, "index"),
        )


@dataclass(frozen=True)
class ResonanceSignal:
    """Layer 4 — chemically meaningful signal created through assignment.

    Never built from a raw unmatched peak: use
    :meth:`from_experimental_peak` (rejects unassigned/ambiguous peaks) or
    :func:`resonance_signals_from_peaks` (skips them). ``atom_refs`` are the
    resolved atom labels; ``group_refs`` the signal-definition references
    (``SignalGroup`` uids / experiment refs) behind the signal.

    Attributes:
        shift_ppm: Observed chemical shift in ppm.
        element: Element symbol the signal belongs to.
        multiplicity: Integral multiplicity (e.g. 3 for CH3).
        atom_refs: Resolved atom labels this signal is assigned to.
        group_refs: Signal-group/experiment references behind the signal.
    """

    shift_ppm: float
    element: str
    multiplicity: int = 1
    atom_refs: tuple[str, ...] = ()
    group_refs: tuple[str, ...] = ()

    @classmethod
    def from_experimental_peak(
        cls,
        peak: ExperimentalPeak,
        *,
        group_refs: tuple[str, ...] = (),
    ) -> ResonanceSignal:
        """Create a resonance from an ASSIGNED experimental peak.

        Raises:
            ValueError: When *peak* is unassigned or ambiguous — a raw
                unmatched peak is never auto-promoted to a resonance.
        """
        if peak.atom_label is None or not peak.assigned:
            raise ValueError(
                f"cannot create ResonanceSignal from unassigned peak at "
                f"{peak.shift_ppm} ppm (element {peak.element!r}, "
                f"label_candidates={peak.label_candidates!r}) — resonances "
                "exist only through assignment"
            )
        return cls(
            shift_ppm=peak.shift_ppm,
            element=normalize_symbol(peak.element),
            multiplicity=int(peak.multiplicity),
            atom_refs=(peak.atom_label,),
            group_refs=tuple(group_refs),
        )

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record (every declared field; refs as lists)."""
        return {
            "shift_ppm": self.shift_ppm,
            "element": self.element,
            "multiplicity": self.multiplicity,
            "atom_refs": list(self.atom_refs),
            "group_refs": list(self.group_refs),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ResonanceSignal:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "ResonanceSignal"
        return cls(
            shift_ppm=_coerce_required_float(payload["shift_ppm"], f"{name}.shift_ppm"),
            element=_coerce_required_str(payload["element"], f"{name}.element"),
            multiplicity=_coerce_required_int(payload["multiplicity"], f"{name}.multiplicity"),
            atom_refs=_coerce_str_tuple(payload["atom_refs"], f"{name}.atom_refs"),
            group_refs=_coerce_str_tuple(payload["group_refs"], f"{name}.group_refs"),
        )


def resonance_signals_from_peaks(peaks: Iterable[ExperimentalPeak]) -> list[ResonanceSignal]:
    """Create resonance signals from ASSIGNED peaks only (G10).

    Unassigned/ambiguous peaks (raw unmatched peaks) are skipped, never
    auto-promoted; the skip count is logged for visibility.
    """
    signals: list[ResonanceSignal] = []
    skipped = 0
    for peak in peaks:
        if not peak.assigned:
            skipped += 1
            continue
        signals.append(ResonanceSignal.from_experimental_peak(peak))
    if skipped:
        logger.info(
            "resonance_signals_from_peaks: skipped %d unassigned peak(s) — "
            "resonances exist only through assignment",
            skipped,
        )
    return signals


@dataclass(frozen=True)
class ProcessedSpectrum:
    """Layer 2 — one processed spectrum: picked peaks + explicit layer records.

    Additively extends the pre-todo-41 shape: the original fields
    (``nucleus``/``element``/``peaks``/``noise``/``reference_shift``/
    ``source_dir``) keep their names, order and meaning, so legacy
    constructors and readers are unchanged. The layer records are optional
    (``None``/empty for hand-built or text-parsed spectra).

    Attributes:
        nucleus: Nucleus label from ``acqus`` (e.g. ``"1H"``).
        element: Element symbol (``"H"`` / ``"C"``).
        peaks: Picked peaks (unassigned, multiplicity from integration).
        noise: Estimated noise level (edge MAD before baseline correction).
        reference_shift: ppm shift applied by manual referencing, if any.
        source_dir: Bruker experiment directory the spectrum came from.
        acquisition: Layer-1 acquisition record (``None`` when unknown).
        processing: Layer-2a processing provenance (``None`` when unknown).
        quality: Layer-2b quality metrics (``None`` when unknown).
        lines: Layer-3 observed/fitted lines (empty when not fitted).
    """

    nucleus: str
    element: str
    peaks: list[ExperimentalPeak]
    noise: float
    reference_shift: float | None = None
    source_dir: str = ""
    acquisition: AcquisitionSpectrum | None = None
    processing: ProcessingProvenance | None = None
    quality: ProcessingQuality | None = None
    lines: list[SpectralLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """JSON-safe record: peaks + every layer block, exact values."""
        return {
            "nucleus": self.nucleus,
            "element": self.element,
            "peaks": [peak.to_dict() for peak in self.peaks],
            "noise": self.noise,
            "reference_shift": self.reference_shift,
            "source_dir": self.source_dir,
            "acquisition": (self.acquisition.to_dict() if self.acquisition is not None else None),
            "processing": (self.processing.to_dict() if self.processing is not None else None),
            "quality": self.quality.to_dict() if self.quality is not None else None,
            "lines": [line.to_dict() for line in self.lines],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ProcessedSpectrum:
        """Rebuild from :meth:`to_dict`; a missing field fails explicitly."""
        _require_payload_fields(payload, cls)
        name = "ProcessedSpectrum"
        peaks_raw = payload["peaks"]
        if not isinstance(peaks_raw, (list, tuple)):
            raise ValueError(f"{name}.peaks must be a list, got {type(peaks_raw).__name__}")
        lines_raw = payload["lines"]
        if not isinstance(lines_raw, (list, tuple)):
            raise ValueError(f"{name}.lines must be a list, got {type(lines_raw).__name__}")
        acquisition_raw = payload["acquisition"]
        processing_raw = payload["processing"]
        quality_raw = payload["quality"]
        return cls(
            nucleus=_coerce_required_str(payload["nucleus"], f"{name}.nucleus"),
            element=_coerce_required_str(payload["element"], f"{name}.element"),
            peaks=[
                ExperimentalPeak.from_dict(_coerce_payload_mapping(peak, f"{name}.peaks[]"))
                for peak in peaks_raw
            ],
            noise=_coerce_required_float(payload["noise"], f"{name}.noise"),
            reference_shift=_payload_float(payload, name, "reference_shift"),
            source_dir=_coerce_required_str(payload["source_dir"], f"{name}.source_dir"),
            acquisition=(
                None
                if acquisition_raw is None
                else AcquisitionSpectrum.from_dict(
                    _coerce_payload_mapping(acquisition_raw, f"{name}.acquisition")
                )
            ),
            processing=(
                None
                if processing_raw is None
                else ProcessingProvenance.from_dict(
                    _coerce_payload_mapping(processing_raw, f"{name}.processing")
                )
            ),
            quality=(
                None
                if quality_raw is None
                else ProcessingQuality.from_dict(
                    _coerce_payload_mapping(quality_raw, f"{name}.quality")
                )
            ),
            lines=[
                SpectralLine.from_dict(_coerce_payload_mapping(line, f"{name}.lines[]"))
                for line in lines_raw
            ],
        )


# --- calculation products ------------------------------------------------


def _tms_source(config: NmrConfig) -> str:
    """Classify where the configured TMS references came from (schema v2).

    Compares the configured 1H/13C values (the only nuclei the Goodman
    table carries) against ``lookup_tms_shieldings`` for this level:
    ``"goodman_tmsdata"`` when every comparable value matches the table,
    ``"custom"`` when any comparable value differs, ``"unknown"`` when the
    table has no row for the level (nothing to compare against).
    """
    sigma_13c, sigma_1h = lookup_tms_shieldings(config.nmr_method, config.nmr_basis, config.solvent)
    table = {"13C": sigma_13c, "1H": sigma_1h}
    comparable = {
        nucleus: sigma
        for nucleus, sigma in table.items()
        if nucleus in config.tms_shieldings and sigma is not None
    }
    if not comparable:
        return "unknown"
    if all(config.tms_shieldings[nucleus] == sigma for nucleus, sigma in comparable.items()):
        return "goodman_tmsdata"
    return "custom"


@dataclass(frozen=True)
class ConformerShielding:
    """Per-conformer Boltzmann weight + parsed shieldings.

    Attributes:
        conformer_id: Conformer identifier (matches ensemble record id).
        boltzmann_weight: Boltzmann weight in ``[0, 1]``. This is the weight
            the averaging/DP5 algorithms actually consume: after the GIAO
            stage it is renormalized over the successful complete
            conformers (todo 30), so reported weights equal algorithm input.
        shieldings: ``{atom_index(0-based): {"symbol", "isotropic", ...}}``.
        log_file: ORCA log path the shieldings were parsed from.
        coordinates: Optional ``(N, 3)`` conformer geometry (CENSO
            screening-level optimised), threaded through for the
            FCHL-weighted DP5 path (DevDoc appendix D).
        symbols: Optional element symbols aligned with *coordinates*.
        delta_hartree: Optional relative energy (Δ under the ensemble's
            unified definition, minimum = 0) — the Boltzmann bookkeeping
            needed to recompute weights at another temperature (todo 30).
    """

    conformer_id: str
    boltzmann_weight: float
    shieldings: dict[int, dict[str, object]]
    log_file: Path | None = None
    coordinates: object | None = None
    symbols: list[str] | None = None
    delta_hartree: float | None = None


# --- signal groups (todo 34 / G08) ------------------------------------------

#: Closed vocabulary of equivalence bases a signal group can be justified by.
#: Defined here — models is the leaf module of the NMR package — and
#: re-exported by :mod:`acp.nmr.equivalence` as ``EQ_BASIS_*`` so the
#: vocabulary has exactly one definition without an import cycle.
EQ_BASIS_TOPOLOGY = "topology"
EQ_BASIS_EXPLICIT = "explicit"
EQ_BASIS_UNKNOWN = "unknown"
SIGNAL_GROUP_BASES: tuple[str, ...] = (EQ_BASIS_TOPOLOGY, EQ_BASIS_EXPLICIT, EQ_BASIS_UNKNOWN)


def _signal_group_uids(value: object, field_name: str) -> tuple[str, ...]:
    """Coerce *value* into a tuple of non-blank strings or raise ``ValueError``."""
    if not isinstance(value, (tuple, list)):
        raise ValueError(
            f"SignalGroup.{field_name} must be a tuple/list of strings, got {type(value).__name__}"
        )
    uids = tuple(value)
    for uid in uids:
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError(
                f"SignalGroup.{field_name} entries must be non-blank strings, got {uid!r}"
            )
    return uids


def _signal_group_coefficients(value: object) -> tuple[float, ...]:
    """Coerce *value* into a tuple of finite floats or raise ``ValueError``."""
    if not isinstance(value, (tuple, list)):
        raise ValueError(
            f"SignalGroup.coefficients must be a tuple/list of numbers, got {type(value).__name__}"
        )
    coefficients: list[float] = []
    for coefficient in value:
        if isinstance(coefficient, bool) or not isinstance(coefficient, (int, float)):
            raise ValueError(
                f"SignalGroup.coefficients entries must be real numbers, got {coefficient!r}"
            )
        number = float(coefficient)
        if not math.isfinite(number):
            raise ValueError(f"SignalGroup.coefficients must be finite, got {coefficient!r}")
        coefficients.append(number)
    return tuple(coefficients)


@dataclass(frozen=True)
class SignalGroup:
    """Full membership of one computed NMR signal (G08).

    Averaging an equivalence group used to keep only its representative
    atom label, so the DP5 per-conformer reconstruction re-read the
    representative's raw shielding and disagreed with the averaged DP4
    residual. A ``SignalGroup`` carries the whole definition — who belongs
    to the signal, with which averaging coefficients, on what equivalence
    basis — so averaging, assignment, DP4 and DP5 all consume ONE signal
    definition and the representative label is a display choice only.

    Attributes:
        atom_uids: Stable member identities — ``NmrStructureMap`` uids
            (``"C:3"``) when a structure map is available, element +
            1-based label uids (``"C1"``) otherwise. Non-empty, no
            duplicates.
        coefficients: Averaging coefficients of the linear combination
            producing the group signal — equal weights (``1/n``) unless a
            group carried explicit coefficients. Finite, same length as
            ``atom_uids``.
        equivalence_basis: Why the members form one signal —
            :data:`EQ_BASIS_TOPOLOGY` (molecular-graph symmetry),
            :data:`EQ_BASIS_EXPLICIT` (user ``EQ:`` assertion) or
            :data:`EQ_BASIS_UNKNOWN` (no justification on record).
        peak_capacity: Number of experimental peaks this signal may occupy
            (integral/multiplicity capacity), or ``None`` when unknown —
            unknown is never assumed to be 1.
        experiment_ref: Stable reference to the experimental observation
            this signal was matched to (``"element:index"``), or ``None``
            when unknown.
    """

    atom_uids: tuple[str, ...]
    coefficients: tuple[float, ...]
    equivalence_basis: str
    peak_capacity: int | None = None
    experiment_ref: str | None = None

    def __post_init__(self) -> None:
        uids = _signal_group_uids(self.atom_uids, "atom_uids")
        if not uids:
            raise ValueError("SignalGroup.atom_uids must be non-empty")
        if len(set(uids)) != len(uids):
            raise ValueError(f"SignalGroup.atom_uids contains duplicates: {uids!r}")
        coefficients = _signal_group_coefficients(self.coefficients)
        if not coefficients:
            raise ValueError("SignalGroup.coefficients must be non-empty")
        if len(coefficients) != len(uids):
            raise ValueError(
                "SignalGroup length mismatch: "
                f"{len(uids)} atom_uids != {len(coefficients)} coefficients"
            )
        if self.equivalence_basis not in SIGNAL_GROUP_BASES:
            raise ValueError(
                f"unknown equivalence basis {self.equivalence_basis!r}; "
                f"expected one of {SIGNAL_GROUP_BASES}"
            )
        if self.peak_capacity is not None:
            if isinstance(self.peak_capacity, bool) or not isinstance(self.peak_capacity, int):
                raise ValueError(
                    f"SignalGroup.peak_capacity must be a positive int or None, "
                    f"got {self.peak_capacity!r}"
                )
            if self.peak_capacity < 1:
                raise ValueError(
                    f"SignalGroup.peak_capacity must be >= 1, got {self.peak_capacity!r}"
                )
        if self.experiment_ref is not None and (
            not isinstance(self.experiment_ref, str) or not self.experiment_ref.strip()
        ):
            raise ValueError(
                f"SignalGroup.experiment_ref must be a non-blank string or None, "
                f"got {self.experiment_ref!r}"
            )
        object.__setattr__(self, "atom_uids", uids)
        object.__setattr__(self, "coefficients", coefficients)

    def as_dict(self) -> dict[str, object]:
        """JSON-safe provenance record (lists, never tuples)."""
        return {
            "atom_uids": list(self.atom_uids),
            "coefficients": list(self.coefficients),
            "equivalence_basis": self.equivalence_basis,
            "peak_capacity": self.peak_capacity,
            "experiment_ref": self.experiment_ref,
        }


@dataclass(frozen=True)
class AtomShift:
    """Per-atom computed chemical shift for one candidate.

    ``signal_group`` carries the full membership the shift was averaged
    from (G08) — ``None`` only for legacy hand-built shifts that never went
    through equivalence-aware averaging.
    """

    atom_index: int
    symbol: str
    nucleus: str
    shielding_ppm: float
    shift_ppm: float
    atom_label: str
    signal_group: SignalGroup | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "atom": self.atom_label,
            "element": self.symbol,
            "calc_ppm": round(self.shift_ppm, 4),
            "signal_group": self.signal_group.as_dict() if self.signal_group is not None else None,
        }


@dataclass(frozen=True)
class Assignment:
    """One (computed, experimental) pair after matching/calibration.

    ``signal_group`` is the full signal definition behind the matched
    representative atom (G08): DP4 residuals and the DP5 per-conformer
    reconstruction read the same group instead of re-deriving a signal
    from the representative label.
    """

    atom_label: str
    element: str
    exp_ppm: float
    calc_ppm: float
    scaled_ppm: float
    residual: float
    signal_group: SignalGroup | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "atom": self.atom_label,
            "element": self.element,
            "exp_ppm": round(self.exp_ppm, 4),
            "calc_ppm": round(self.calc_ppm, 4),
            # schema v2 (todo 24): the RAW field value — never a display
            # rounding; the XLSX sheet writes this same float so JSON and
            # XLSX carry identical numbers (gap §8.2).
            "scaled_ppm": self.scaled_ppm,
            "residual": round(self.residual, 4),
            "signal_group": self.signal_group.as_dict() if self.signal_group is not None else None,
        }


@dataclass(frozen=True)
class RegressionResult:
    """Linear regression fit for one nucleus."""

    nucleus: str
    slope: float
    intercept: float
    r_squared: float
    mae: float

    def as_dict(self) -> dict[str, object]:
        return {
            "slope": round(self.slope, 6),
            "intercept": round(self.intercept, 6),
            "r_squared": round(self.r_squared, 6),
            "mae": round(self.mae, 6),
        }


# --- evidence gate (todo 8 / G05) ------------------------------------------


EvidenceStatus = Literal["valid", "invalid", "evidence_insufficient"]

EVIDENCE_STATUSES: tuple[str, ...] = ("valid", "invalid", "evidence_insufficient")


@dataclass(frozen=True)
class NucleusEvidence:
    """Expected vs actually matched signal counts for one nucleus (G05).

    Attributes:
        expected: Number of experimental observations (peaks) requested
            for this nucleus in the configured nuclei list.
        matched: Number of residuals actually produced for this nucleus.
    """

    expected: int
    matched: int

    def as_dict(self) -> dict[str, int]:
        return {"expected": self.expected, "matched": self.matched}


@dataclass(frozen=True)
class CandidateEvidence:
    """Evidence record that gates one candidate into/out of DP4 ranking (G05).

    Attributes:
        status: ``valid`` (rankable), ``invalid`` (no matched signals at all
            or a nucleus set incomparable with the other candidates) or
            ``evidence_insufficient`` (1–2 matched signals — a two-point
            calibration cannot support ranking).
        per_nucleus: Expected/matched counts keyed by configured nucleus.
        observation_ids: Stable ids of the matched experimental peaks
            (T6 peak identity, ``"element:index"``).
        exclusion_reasons: Closed-vocabulary reasons for a non-valid
            status; empty for valid candidates.
        total_matched: Sum of matched residuals over the configured nuclei.
    """

    status: EvidenceStatus
    per_nucleus: dict[str, NucleusEvidence]
    observation_ids: tuple[str, ...]
    exclusion_reasons: tuple[str, ...]
    total_matched: int

    def __post_init__(self) -> None:
        if self.status not in EVIDENCE_STATUSES:
            raise ValueError(f"unknown evidence status: {self.status!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "total_matched": self.total_matched,
            "observation_ids": list(self.observation_ids),
            "exclusion_reasons": list(self.exclusion_reasons),
            "per_nucleus": {nuc: ev.as_dict() for nuc, ev in self.per_nucleus.items()},
        }


# --- typed probability state (todo 10 / G07 / gap §8.2) ---------------------


ProbabilityStatus = Literal[
    "valid",
    "invalid",
    "evidence_insufficient",
    "unavailable",
    "not_applicable",
    "placeholder",
]

PROBABILITY_STATUSES: tuple[str, ...] = (
    "valid",
    "invalid",
    "evidence_insufficient",
    "unavailable",
    "not_applicable",
    "placeholder",
)


@dataclass(frozen=True)
class ProbabilityResult:
    """Immutable per-model probability state for one candidate (gap §8.2).

    Attributes:
        model_id: Model identity (``goodman-dp4`` / ``goodman-dp5`` /
            ``placeholder-dp5``); must be non-blank.
        model_version: Actual error-model identifier backing this result.
        status: Closed ``ProbabilityStatus`` vocabulary — ``valid``,
            ``invalid``, ``evidence_insufficient``, ``unavailable``,
            ``not_applicable`` or ``placeholder``.
        probability: The probability value, or ``None`` when no probability
            exists — serialization must keep ``None`` (JSON ``null``),
            never coerce it to ``0``.
        mode: Model execution mode (e.g. DP5 ``fchl``/``fallback``) or
            ``None`` when the model never ran.
        calibration_status: Calibration/applicability state, independent
            of ``status`` (a valid result is not automatically calibrated).
        reasons: Closed-vocabulary reasons for non-valid states; empty
            otherwise.
        atom_diagnostics: Optional per-atom diagnostic records (T16).
    """

    model_id: str
    model_version: str
    status: ProbabilityStatus
    probability: float | None
    mode: str | None
    calibration_status: str
    reasons: tuple[str, ...] = ()
    atom_diagnostics: tuple[dict[str, object], ...] = ()

    def __post_init__(self) -> None:
        if not self.model_id.strip():
            raise ValueError("probability model_id must be a non-blank string")
        if self.status not in PROBABILITY_STATUSES:
            raise ValueError(f"unknown probability status: {self.status!r}")

    def as_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "status": self.status,
            # None is preserved as-is — JSON null, never 0
            "probability": self.probability,
            "mode": self.mode,
            "calibration_status": self.calibration_status,
            "reasons": list(self.reasons),
            "atom_diagnostics": [dict(d) for d in self.atom_diagnostics],
        }


@dataclass(frozen=True)
class CandidateProbability:
    """DP4 + DP5 typed probability state for one candidate (gap §8.2)."""

    dp4: ProbabilityResult
    dp5: ProbabilityResult

    def as_dict(self) -> dict[str, object]:
        return {"dp4": self.dp4.as_dict(), "dp5": self.dp5.as_dict()}


# --- ensemble quality gate (todo 30 / gap G09) ------------------------------

#: Energy definitions an ensemble can be compared on. ``none`` = no record
#: carried a usable energy — nothing may be selected (missing is never 0).
ENERGY_DEFINITIONS: tuple[str, ...] = ("free_energy", "energy", "none")

#: Closed vocabulary of per-conformer exclusion reasons (G09). ``None`` on a
#: successful conformer; every non-selected / failed conformer carries one.
CONFORMER_EXCLUSION_REASONS: tuple[str, ...] = (
    "missing_energy",  # no value under the ensemble's unified definition
    "outside_energy_window",
    "population_threshold",  # cumulative-population gate (engineering target)
    "resource_cap",  # hard max_conformers cap — uncovered mass is recorded
    "giao_failed",  # QC produced no usable result
    "incomplete_shieldings",  # required-nuclei atoms missing from the parse
)

#: Closed vocabulary of ensemble-quality facts. Facts only — a flag never
#: claims solution coverage; it marks engineering-visible quality losses.
ENSEMBLE_QUALITY_FLAGS: tuple[str, ...] = (
    "energy_definition_incomplete",
    "resource_cap_truncated",
    "population_gate_applied",
    "dominant_conformer_failed",
    "successful_population_below_target",
    "tail_conformer_failed",
)

ENSEMBLE_QUALITY_STATUSES: tuple[str, ...] = ("ok", "degraded")


@dataclass(frozen=True)
class ConformerPopulation:
    """Per-conformer ensemble-selection evidence (todo 30 / G09).

    Populations are relative to the discovered ensemble — NOT solution
    coverage: a well-covered discovered ensemble says nothing about
    conformers the search never found.

    Attributes:
        conformer_id: Stable id (``conf_<discovered index>``).
        energy_definition: Definition the ensemble was compared on
            (:data:`ENERGY_DEFINITIONS`).
        energy_hartree: Value under that definition, or ``None`` when the
            record carried no usable value (never 0-filled).
        delta_hartree: Relative energy (minimum = 0), ``None`` when missing.
        raw_weight: Boltzmann weight over the energy-valid records.
        selected_weight: Weight normalized over the selected set (the GIAO
            input), ``None`` when not selected.
        final_weight: Weight normalized over the successful complete
            conformers (what averaging/DP5 actually consumed).
        exclusion_reason: Closed reason or ``None`` when successful.
    """

    conformer_id: str
    energy_definition: str
    energy_hartree: float | None
    delta_hartree: float | None
    raw_weight: float | None
    selected_weight: float | None
    final_weight: float | None
    exclusion_reason: str | None

    def __post_init__(self) -> None:
        if self.energy_definition not in ENERGY_DEFINITIONS:
            raise ValueError(
                f"unknown energy definition {self.energy_definition!r}; "
                f"expected one of {ENERGY_DEFINITIONS}"
            )
        if self.exclusion_reason is not None and self.exclusion_reason not in (
            CONFORMER_EXCLUSION_REASONS
        ):
            raise ValueError(
                f"unknown conformer exclusion reason {self.exclusion_reason!r}; "
                f"expected one of {CONFORMER_EXCLUSION_REASONS}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "conformer_id": self.conformer_id,
            "energy_definition": self.energy_definition,
            "energy_hartree": self.energy_hartree,
            "delta_hartree": self.delta_hartree,
            "raw_weight": round(self.raw_weight, 6) if self.raw_weight is not None else None,
            "selected_weight": (
                round(self.selected_weight, 6) if self.selected_weight is not None else None
            ),
            "final_weight": (
                round(self.final_weight, 6) if self.final_weight is not None else None
            ),
            "exclusion_reason": self.exclusion_reason,
        }


@dataclass(frozen=True)
class EnsembleQuality:
    """Ensemble population/quality evidence for one candidate (G09).

    Population denominators (documented, never conflated):
    ``preselection_population`` = count fraction of discovered records with
    a usable energy; ``selected_population`` = Boltzmann mass fraction of
    the energy-valid ensemble that entered GIAO; ``successful_population`` =
    selected-weight fraction whose shieldings completed and were complete.
    ``uncovered_population`` = energy-valid mass dropped by the hard
    ``max_conformers`` cap; ``population_gate_dropped`` = mass intentionally
    dropped by the cumulative-population target.

    ``quality_status`` is ``degraded`` when a dominant (selected weight
    ≥ 0.5) conformer failed, when the successful population fell below the
    engineering target, or when the cap left more than the uncovered-mass
    limit outside the analysis.
    """

    energy_definition: str
    n_discovered: int
    n_selected: int
    n_successful: int
    preselection_population: float
    selected_population: float
    successful_population: float
    uncovered_population: float
    population_gate_dropped: float
    quality_status: str
    quality_flags: tuple[str, ...]
    conformers: tuple[ConformerPopulation, ...]

    def __post_init__(self) -> None:
        if self.energy_definition not in ENERGY_DEFINITIONS:
            raise ValueError(
                f"unknown energy definition {self.energy_definition!r}; "
                f"expected one of {ENERGY_DEFINITIONS}"
            )
        if self.quality_status not in ENSEMBLE_QUALITY_STATUSES:
            raise ValueError(
                f"unknown ensemble quality status {self.quality_status!r}; "
                f"expected one of {ENSEMBLE_QUALITY_STATUSES}"
            )
        unknown = [flag for flag in self.quality_flags if flag not in ENSEMBLE_QUALITY_FLAGS]
        if unknown:
            raise ValueError(
                f"unknown ensemble quality flag(s) {unknown}; "
                f"expected subset of {ENSEMBLE_QUALITY_FLAGS}"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "energy_definition": self.energy_definition,
            "n_discovered": self.n_discovered,
            "n_selected": self.n_selected,
            "n_successful": self.n_successful,
            "preselection_population": round(self.preselection_population, 6),
            "selected_population": round(self.selected_population, 6),
            "successful_population": round(self.successful_population, 6),
            "uncovered_population": round(self.uncovered_population, 6),
            "population_gate_dropped": round(self.population_gate_dropped, 6),
            "quality_status": self.quality_status,
            "quality_flags": list(self.quality_flags),
            "conformers": [c.as_dict() for c in self.conformers],
        }


@dataclass
class CandidateResult:
    """Full per-candidate analysis (stages 4–7 product).

    ``dp4_probability``/``dp5_probability`` default to ``None`` — a missing
    probability is never fabricated as ``0.0``; candidates excluded by the
    evidence gate keep ``None``. ``probability`` layers the typed state
    (status/mode/calibration/reasons) on top of the flat fields; it stays
    ``None`` only for legacy callers that never attach it.
    """

    index: int
    label: str
    atom_shifts: list[AtomShift] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    regressions: dict[str, RegressionResult] = field(default_factory=dict)
    dp4_probability: float | None = None
    dp5_probability: float | None = None
    # Placeholder-path output under its own name (todo 13): never serialized
    # as a probability and never used for ranking — ``dp5_probability`` stays
    # None whenever the real Goodman DP5 model did not produce a value.
    dp5_diagnostic_score: float | None = None
    # FCHL kernel backend that produced this candidate's DP5 value ("qml" |
    # "numpy"), or None when no FCHL kernel ran (G07 — per candidate, never
    # shared model state).
    dp5_kernel: str | None = None
    conformer_shieldings: list[ConformerShielding] = field(default_factory=list)
    evidence: CandidateEvidence | None = None
    probability: CandidateProbability | None = None
    ensemble_quality: EnsembleQuality | None = None
    #: Explicit record of the signal definitions this candidate consumed
    #: (todo 34 / G08), populated by the workflow from the averaged shifts.
    #: Empty for hand-built/legacy candidates — :meth:`signal_groups_used`
    #: then derives the record from assignments/atom_shifts instead of
    #: pretending there were no signals.
    signal_groups: tuple[SignalGroup, ...] = ()

    def signal_groups_used(self) -> tuple[SignalGroup, ...]:
        """Return the signal definitions this candidate consumed (G08).

        Prefers the explicit ``signal_groups`` record. When a caller only
        threaded groups through ``assignments``/``atom_shifts``, they are
        derived in first-appearance order (deduplicated by equality) so the
        record is never empty just because the field was not populated.
        """
        if self.signal_groups:
            return self.signal_groups
        derived: list[SignalGroup] = []
        seen: set[SignalGroup] = set()
        for assignment in self.assignments:
            group = assignment.signal_group
            if group is not None and group not in seen:
                seen.add(group)
                derived.append(group)
        if derived:
            return tuple(derived)
        for shift in self.atom_shifts:
            group = shift.signal_group
            if group is not None and group not in seen:
                seen.add(group)
                derived.append(group)
        return tuple(derived)

    def analysis_status(self) -> str | None:
        """Schema-v2 per-candidate status (gap §8.2).

        Evidence-gate status when the gate ran; otherwise the typed DP4
        status (legacy callers that only attach a probability block);
        ``None`` = unknown — a historical/foreign candidate is never
        auto-upgraded to a status it never recorded.
        """
        if self.evidence is not None:
            return self.evidence.status
        if self.probability is not None:
            return self.probability.dp4.status
        return None

    def coverage(self) -> dict[str, object]:
        """Schema-v2 coverage record (gap §8.2; todo 24, populated by T30).

        Signal counts come from the evidence gate (``expected_signals`` =
        Σ per-nucleus expected, ``matched_signals`` = ``total_matched``);
        both (plus ``per_nucleus``) are ``None`` when no evidence was
        attached — never coerced to 0. Conformer population: when the
        ensemble-quality record is attached (workflow runs, todo 30) the
        pre-GIAO selected set is real — ``n_conformers_selected`` and
        ``selected_population`` (energy-valid Boltzmann mass fraction) come
        from it, and ``successful_population`` is the selected-weight
        fraction whose shieldings completed and were complete. Without the
        record (hand-built candidates) the legacy view stays: selected
        fields ``None`` (never invented) and ``successful_population`` =
        sum of the stored conformer weights.
        """
        if self.evidence is not None:
            expected: int | None = sum(ev.expected for ev in self.evidence.per_nucleus.values())
            matched: int | None = self.evidence.total_matched
            per_nucleus: dict[str, object] | None = {
                nucleus: ev.as_dict() for nucleus, ev in self.evidence.per_nucleus.items()
            }
        else:
            expected = None
            matched = None
            per_nucleus = None
        quality = self.ensemble_quality
        return {
            "expected_signals": expected,
            "matched_signals": matched,
            "per_nucleus": per_nucleus,
            "n_conformers_selected": quality.n_selected if quality is not None else None,
            "n_conformers_successful": (
                quality.n_successful if quality is not None else len(self.conformer_shieldings)
            ),
            "selected_population": (
                round(quality.selected_population, 6) if quality is not None else None
            ),
            "successful_population": (
                round(quality.successful_population, 6)
                if quality is not None
                else round(sum(cs.boltzmann_weight for cs in self.conformer_shieldings), 6)
            ),
        }

    def as_dict(self) -> dict[str, object]:
        regression_obj: dict[str, object] = {}
        for nucleus, regression in self.regressions.items():
            regression_obj[nucleus] = regression.as_dict()
        return {
            "index": self.index,
            "label": self.label,
            "dp4_probability": (
                round(self.dp4_probability, 6) if self.dp4_probability is not None else None
            ),
            "dp5_probability": (
                round(self.dp5_probability, 6) if self.dp5_probability is not None else None
            ),
            "dp5_diagnostic_score": (
                round(self.dp5_diagnostic_score, 6)
                if self.dp5_diagnostic_score is not None
                else None
            ),
            "dp5_kernel": self.dp5_kernel,
            "evidence": self.evidence.as_dict() if self.evidence is not None else None,
            "probability": self.probability.as_dict() if self.probability is not None else None,
            "analysis_status": self.analysis_status(),
            "coverage": self.coverage(),
            "ensemble_quality": (
                self.ensemble_quality.as_dict() if self.ensemble_quality is not None else None
            ),
            "signal_groups": [group.as_dict() for group in self.signal_groups_used()],
            "n_conformers": len(self.conformer_shieldings),
            "regression": regression_obj,
            "assignment": [a.as_dict() for a in self.assignments],
            "conformers": [
                {
                    "id": cs.conformer_id,
                    "boltzmann_weight": round(cs.boltzmann_weight, 6),
                }
                for cs in self.conformer_shieldings
            ],
        }


def _dp4_rank_key(candidate: CandidateResult) -> tuple[float, float, int]:
    """Sort key for DP4 ranking: ``(DP4, DP5, -index)`` — missing sorts last.

    DP4 is the primary key (``None`` → ``-inf``, never wins). DP5 enters the
    tie-break ONLY as a real, valid probability: the typed block must say
    ``probability.dp5.status == "valid"`` AND ``dp5_probability`` must be
    set. Placeholder / diagnostic / unavailable / not_applicable / invalid
    DP5 — including a stale float left on the flat field while the typed
    block says otherwise — is treated as missing so it can never decide a
    tie. The final ``-index`` component settles a full tie deterministically
    in favour of the smallest candidate index (documented rule: input order
    never decides ambiguity unpredictably).
    """
    dp4 = candidate.dp4_probability
    probability = candidate.probability
    dp5 = (
        candidate.dp5_probability
        if probability is not None
        and probability.dp5.status == "valid"
        and candidate.dp5_probability is not None
        else None
    )
    return (
        dp4 if dp4 is not None else float("-inf"),
        dp5 if dp5 is not None else float("-inf"),
        -candidate.index,
    )


#: nmr_report.json serialization schema version. v2 = todo 24: full
#: effective config, per-candidate analysis_status/coverage, calibration
#: provenance, raw scaled_ppm. v1 payloads (no ``schema_version`` key) are
#: historical reports and stay read-only — see
#: :func:`acp.nmr.report.report_validation_note`.
REPORT_SCHEMA_VERSION: int = 2


@dataclass
class NmrReport:
    """Top-level report across all candidates (stage 8 input)."""

    candidates: list[CandidateResult] = field(default_factory=list)
    config: NmrConfig = field(default_factory=NmrConfig)
    error_model: str = "goodman-legacy"
    dp5_mode: str = "fallback"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ranked_candidates(self) -> list[CandidateResult]:
        """Candidates eligible for DP4 ranking (G05).

        A candidate ranks only when its evidence is valid (or unset for
        legacy callers) AND it carries a DP4 probability — invalid /
        evidence-insufficient candidates never compete. When a typed
        probability block is attached it must also say ``dp4.status ==
        "valid"``: a stale float on the flat field can never rank a
        candidate whose typed DP4 state is anything else.
        """
        return [
            candidate
            for candidate in self.candidates
            if (candidate.evidence is None or candidate.evidence.status == "valid")
            and candidate.dp4_probability is not None
            and (candidate.probability is None or candidate.probability.dp4.status == "valid")
        ]

    @property
    def winner(self) -> CandidateResult | None:
        """Return the highest-DP4 rankable candidate.

        Ties are broken by a REAL valid DP5 only (see ``_dp4_rank_key``),
        then by the smallest candidate index; unavailable/placeholder DP5
        never influences the order. Candidates excluded by the evidence gate
        (or without a probability) are never eligible; ``None`` when nobody
        qualifies — a winner is never invented.
        """
        ranked = self.ranked_candidates
        if not ranked:
            return None
        return max(ranked, key=_dp4_rank_key)

    @property
    def dp4_ranking(self) -> str:
        """``"normal"`` when ≥2 candidates are ranked, else ``"not_applicable"``."""
        return "normal" if len(self.ranked_candidates) >= 2 else "not_applicable"

    def _placeholder_note_active(self) -> bool:
        """Whether the placeholder warning applies (typed state wins).

        Reads each candidate's ``probability.dp5.status``; only when no
        candidate carries typed state (legacy callers) does it fall back to
        the ``error_model`` prefix check.
        """
        typed = [c.probability for c in self.candidates if c.probability is not None]
        if typed:
            return any(p.dp5.status == "placeholder" for p in typed)
        return self.error_model.startswith("placeholder")

    def _calibration_status(self, side: Literal["dp4", "dp5"]) -> str | None:
        """Aggregate one probability side's calibration_status (gap §8.2).

        ``None`` when no candidate carries a typed block; the single value
        when all agree; ``"mixed"`` when candidates disagree — the
        per-candidate values always stay visible in each probability block.
        """
        statuses = {
            (c.probability.dp4 if side == "dp4" else c.probability.dp5).calibration_status
            for c in self.candidates
            if c.probability is not None
        }
        if not statuses:
            return None
        return next(iter(statuses)) if len(statuses) == 1 else "mixed"

    def _provenance(self) -> dict[str, object]:
        """Calibration provenance block (schema v2 / gap §8.2).

        Records which error model ran, the aggregated per-side
        calibration status, the TMS reference values in force and where
        they came from (``goodman_tmsdata`` = configured values equal the
        Goodman table row for this level, ``custom`` = some comparable
        value differs, ``unknown`` = the table has no row for the level —
        classification only compares 1H/13C, the only nuclei the table
        carries).
        """
        return {
            "error_model": self.error_model,
            "dp5_mode": self.dp5_mode,
            "calibration_status": {
                "dp4": self._calibration_status("dp4"),
                "dp5": self._calibration_status("dp5"),
            },
            "tms_references": dict(self.config.tms_shieldings),
            "tms_source": _tms_source(self.config),
            # todo 29: six-segment protocol record + verdict (workflow-populated;
            # None only for hand-built reports that carry no run context).
            "protocol": self.metadata.get("protocol"),
        }

    def as_dict(self) -> dict[str, object]:
        winner = self.winner
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            # todo 29: protocol identity = aggregated per-candidate spec
            # fingerprints (workflow-populated via NmrReport.metadata;
            # None only for hand-built reports without run context).
            "protocol_id": self.metadata.get("protocol_id"),
            "summary": {
                "n_candidates": len(self.candidates),
                "winner": (
                    {
                        "index": winner.index,
                        "label": winner.label,
                        "dp4": (
                            round(winner.dp4_probability, 6)
                            if winner.dp4_probability is not None
                            else None
                        ),
                        "dp5": (
                            round(winner.dp5_probability, 6)
                            if winner.dp5_probability is not None
                            else None
                        ),
                    }
                    if winner is not None
                    else None
                ),
                "dp4_ranking": self.dp4_ranking,
                "nuclei": list(self.config.nuclei),
            },
            "candidates": [c.as_dict() for c in self.candidates],
            # schema v2: the full T21 effective-config record — a strict
            # superset of the old {nmr_method, nmr_basis, solvent} block, so
            # existing readers keep those keys unchanged.
            "config": self.config.to_dict(),
            "error_model": self.error_model,
            "dp5_mode": self.dp5_mode,
            "fchl_kernel": self.metadata.get("fchl_kernel", ""),
            "provenance": self._provenance(),
            # todo 30: leave-one-conformer-out / temperature winner-stability
            # verdict (workflow-populated; None for hand-built reports).
            "sensitivity": self.metadata.get("sensitivity"),
            "note": (
                "DP4/DP5 use placeholder error-model parameters (P1a); "
                "values are relative only — do not use for publication."
            )
            if self._placeholder_note_active()
            else "",
        }


__all__ = [
    "ExperimentalPeak",
    "ExperimentalNmr",
    "AcquisitionSpectrum",
    "ProcessingProvenance",
    "ProcessingQuality",
    "SpectralLine",
    "ResonanceSignal",
    "ProcessedSpectrum",
    "resonance_signals_from_peaks",
    "ParseIssue",
    "ParseIssueCode",
    "PARSE_ISSUE_CODES",
    "NmrConfig",
    "REPORT_SCHEMA_VERSION",
    "ConformerShielding",
    "SignalGroup",
    "SIGNAL_GROUP_BASES",
    "EQ_BASIS_TOPOLOGY",
    "EQ_BASIS_EXPLICIT",
    "EQ_BASIS_UNKNOWN",
    "AtomShift",
    "Assignment",
    "RegressionResult",
    "ProbabilityStatus",
    "PROBABILITY_STATUSES",
    "ProbabilityResult",
    "CandidateProbability",
    "ENERGY_DEFINITIONS",
    "CONFORMER_EXCLUSION_REASONS",
    "ENSEMBLE_QUALITY_FLAGS",
    "ENSEMBLE_QUALITY_STATUSES",
    "ConformerPopulation",
    "EnsembleQuality",
    "CandidateResult",
    "NmrReport",
    "normalize_symbol",
    "nucleus_label",
    "element_of_nucleus",
]
