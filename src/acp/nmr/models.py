# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false
"""NMR + DP4/DP5 domain models.

The dataclasses in this module are the in-memory representation that flows
between the stages of the NMR workflow (DevDoc §5): experimental input,
per-conformer shieldings, Boltzmann-averaged candidate shieldings, the
assignment / scaling products, and the final DP4/DP5 probabilities.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

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


@dataclass(frozen=True)
class ExperimentalPeak:
    """One experimental resonance.

    Attributes:
        shift_ppm: Chemical shift in ppm.
        atom_label: Atom assignment (e.g. ``"C1"``). ``None`` for unassigned.
        multiplicity: Integral multiplicity (e.g. 3 for CH3). Defaults to 1.
        element: Element this peak belongs to (``"H"``/``"C"``/...).
    """

    shift_ppm: float
    element: str
    atom_label: str | None = None
    multiplicity: int = 1


@dataclass
class ExperimentalNmr:
    """Parsed experimental NMR input (DevDoc §6.2).

    Attributes:
        peaks: Peaks grouped by element (``"H"``/``"C"`` → list of peaks).
        equivalence_groups: Equivalence groups (lists of atom labels); each
            group is averaged to a single computed signal before matching.
        omit_atoms: Atom labels excluded from comparison.
        assigned: ``True`` when peaks carry explicit atom assignments.
    """

    peaks: dict[str, list[ExperimentalPeak]] = field(default_factory=dict)
    equivalence_groups: list[list[str]] = field(default_factory=list)
    omit_atoms: list[str] = field(default_factory=list)
    assigned: bool = False

    def nuclei(self) -> list[str]:
        """Return the sorted element symbols actually present."""
        return sorted(self.peaks.keys())

    def peaks_for(self, element: str) -> list[ExperimentalPeak]:
        """Return the peaks for one element (normalized)."""
        return self.peaks.get(normalize_symbol(element), [])


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

    def tms_for(self, nucleus: str) -> float | None:
        """Return the TMS reference shielding for a nucleus label."""
        return self.tms_shieldings.get(nucleus)

    def element_nuclei(self, symbols: list[str]) -> list[str]:
        """Return the configured nuclei whose element is present in *symbols*."""
        present = {normalize_symbol(s) for s in symbols}
        return [n for n in self.nuclei if element_of_nucleus(n) in present]


# --- calculation products ------------------------------------------------


@dataclass(frozen=True)
class ConformerShielding:
    """Per-conformer Boltzmann weight + parsed shieldings.

    Attributes:
        conformer_id: Conformer identifier (matches ensemble record id).
        boltzmann_weight: Boltzmann weight in ``[0, 1]``.
        shieldings: ``{atom_index(0-based): {"symbol", "isotropic", ...}}``.
        log_file: ORCA log path the shieldings were parsed from.
        coordinates: Optional ``(N, 3)`` conformer geometry (CENSO
            screening-level optimised), threaded through for the
            FCHL-weighted DP5 path (DevDoc appendix D).
        symbols: Optional element symbols aligned with *coordinates*.
    """

    conformer_id: str
    boltzmann_weight: float
    shieldings: dict[int, dict[str, object]]
    log_file: Path | None = None
    coordinates: object | None = None
    symbols: list[str] | None = None


@dataclass(frozen=True)
class AtomShift:
    """Per-atom computed chemical shift for one candidate."""

    atom_index: int
    symbol: str
    nucleus: str
    shielding_ppm: float
    shift_ppm: float
    atom_label: str

    def as_dict(self) -> dict[str, object]:
        return {
            "atom": self.atom_label,
            "element": self.symbol,
            "calc_ppm": round(self.shift_ppm, 4),
        }


@dataclass(frozen=True)
class Assignment:
    """One (computed, experimental) pair after matching/calibration."""

    atom_label: str
    element: str
    exp_ppm: float
    calc_ppm: float
    scaled_ppm: float
    residual: float

    def as_dict(self) -> dict[str, object]:
        return {
            "atom": self.atom_label,
            "element": self.element,
            "exp_ppm": round(self.exp_ppm, 4),
            "calc_ppm": round(self.calc_ppm, 4),
            "residual": round(self.residual, 4),
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


@dataclass
class CandidateResult:
    """Full per-candidate analysis (stages 4–7 product)."""

    index: int
    label: str
    atom_shifts: list[AtomShift] = field(default_factory=list)
    assignments: list[Assignment] = field(default_factory=list)
    regressions: dict[str, RegressionResult] = field(default_factory=dict)
    dp4_probability: float = 0.0
    dp5_probability: float = 0.0
    conformer_shieldings: list[ConformerShielding] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        regression_obj: dict[str, object] = {}
        for nucleus, regression in self.regressions.items():
            regression_obj[nucleus] = regression.as_dict()
        return {
            "index": self.index,
            "label": self.label,
            "dp4_probability": round(self.dp4_probability, 6),
            "dp5_probability": round(self.dp5_probability, 6),
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


@dataclass
class NmrReport:
    """Top-level report across all candidates (stage 8 input)."""

    candidates: list[CandidateResult] = field(default_factory=list)
    config: NmrConfig = field(default_factory=NmrConfig)
    error_model: str = "goodman-legacy"
    dp5_mode: str = "fallback"
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def winner(self) -> CandidateResult | None:
        """Return the highest-DP4 candidate (ties broken by DP5)."""
        if not self.candidates:
            return None
        return max(self.candidates, key=lambda c: (c.dp4_probability, c.dp5_probability))

    def as_dict(self) -> dict[str, object]:
        winner = self.winner
        return {
            "summary": {
                "n_candidates": len(self.candidates),
                "winner": (
                    {
                        "index": winner.index,
                        "label": winner.label,
                        "dp4": round(winner.dp4_probability, 6),
                        "dp5": round(winner.dp5_probability, 6),
                    }
                    if winner is not None
                    else None
                ),
                "nuclei": list(self.config.nuclei),
            },
            "candidates": [c.as_dict() for c in self.candidates],
            "config": {
                "nmr_method": self.config.nmr_method,
                "nmr_basis": self.config.nmr_basis,
                "solvent": self.config.solvent,
            },
            "error_model": self.error_model,
            "dp5_mode": self.dp5_mode,
            "fchl_kernel": self.metadata.get("fchl_kernel", ""),
            "note": (
                "DP4/DP5 use placeholder error-model parameters (P1a); "
                "values are relative only — do not use for publication."
            )
            if self.error_model.startswith("placeholder")
            else "",
        }


__all__ = [
    "ExperimentalPeak",
    "ExperimentalNmr",
    "NmrConfig",
    "ConformerShielding",
    "AtomShift",
    "Assignment",
    "RegressionResult",
    "CandidateResult",
    "NmrReport",
    "normalize_symbol",
    "nucleus_label",
    "element_of_nucleus",
]
