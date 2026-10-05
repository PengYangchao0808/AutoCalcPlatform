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

    def as_dict(self) -> dict[str, object]:
        winner = self.winner
        return {
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
            if self._placeholder_note_active()
            else "",
        }


__all__ = [
    "ExperimentalPeak",
    "ExperimentalNmr",
    "ParseIssue",
    "ParseIssueCode",
    "PARSE_ISSUE_CODES",
    "NmrConfig",
    "ConformerShielding",
    "AtomShift",
    "Assignment",
    "RegressionResult",
    "ProbabilityStatus",
    "PROBABILITY_STATUSES",
    "ProbabilityResult",
    "CandidateProbability",
    "CandidateResult",
    "NmrReport",
    "normalize_symbol",
    "nucleus_label",
    "element_of_nucleus",
]
