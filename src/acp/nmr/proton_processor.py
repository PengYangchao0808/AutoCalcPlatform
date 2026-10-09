# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
"""Proton-spectrum multiplet processor (todo 44 / G10).

Consumes the layer-2 :class:`~acp.nmr.models.ProcessedSpectrum` (picked lines
with per-line integrals/widths) and produces chemically meaningful multiplets:
BIC-driven grouping of adjacent lines into first-order multiplets, structure-
related total-H / methyl (3H) constraints, explicit overlap uncertainty with
alternative groupings, and a per-multiplet peak-capacity export consumable as
``acp.nmr.iterative_assignment.PeakConstraint`` (todo 46 semantics).

Why not the midpoint heuristic
------------------------------
``spectra.py::_integrate_multiplicities`` integrates every line between its
neighbour midpoints and rounds the smallest area to multiplicity 1.  A 1:2:1
triplet then carries per-line multiplicities ``[1, 2, 1]`` (sum 4, not 3) and
a 1:3:3:1 quartet ``[1, 3, 3, 1]`` (sum 8, not 2), so neither the molecule's
total-H count nor a 3H methyl group can be recovered from the line list.  The
processor groups lines into multiplets first and assigns capacity to the
*group*, then reconciles group integrals with the structure-related total
proton count (G10).

Model selection (BIC)
---------------------
Grouping is a contiguous-segmentation model selection over a region's line
list.  A segment (candidate multiplet) is modelled as a first-order multiplet:
positions on an arithmetic progression with constant ``J`` (``0 < J <=``
``max_coupling_hz``), an unimodal intensity envelope, and homogeneous line
widths.  For a partition ``P`` into ``k`` segments::

    BIC(P) = sum_g [ m_g * ln(RSS_g / m_g + floor^2) + shape penalties ]
             + bic_penalty * ln(m_region) * k

``floor^2`` (``min_residual_ppm``^2) is the position-model noise floor so a
2-line segment is not free; the per-segment ``bic_penalty * ln(N)`` charge is
the complexity term ``ln(N)*k`` of the upstream Goodman code (Goodman-lab/DP5
pinned commit ``b6cf559`` ``Proton_processing.py``: ``BIC = N*ln(RSS/N) +
ln(N)*(3*n_peaks+2)`` with a 15-unit removal margin; indexed in gap doc 12.2 -
not vendored in this repo).  On top of the contiguous segmentation, an
arithmetic-progression extraction pass recovers multiplets whose lines are
*interleaved* with another multiplet (e.g. a methoxy singlet inside a
quartet): lines within ``ap_tolerance_ppm`` of a lattice with step ``J``
(pair support >= 3) form one multiplet and the remainder is re-partitioned
contiguously.  Every candidate grouping is scored with the same BIC; the best
is primary and the rest are reported as alternatives when the data does not
decide (``ambiguity_margin``).

Constraints
-----------
``total_hydrogens`` is a first-class input.  Where provided, group atom counts
are integer-allocated so their sum equals the total exactly (largest-remainder
allocation; upstream ``sum_round`` analogue), with recognised methyls pinned
to 3H.  Methyls are recognised explicitly (``methyl_line_indices`` /
``methyl_peak_indices``) or by integration ~= 3x the reference unit.  When the
total is absent the constraint is *reported as unavailable*, never silently
ignored; counts fall back to integral ratios.

Overlap uncertainty
-------------------
Overlapping / ambiguous regions carry a :class:`ProtonOverlapRegion` with
``resolved``, a closed ``kind`` and the alternative groupings.  ``slots``
(shared-peak capacity) is raised to 2 for unresolved regions so the assignment
layer can host several signals on the region's peaks.

Three-state rule: synthetic hand-built and synthetic-FID layers are binding;
real instrument data is ``NOT_VERIFIED`` until a real Bruker proton dataset
ships (see tests/test_acp_nmr_proton_processor.py).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

from acp.nmr.models import ProcessedSpectrum, normalize_symbol

logger = logging.getLogger(__name__)

PROCESSOR_ID = "proton_processor_v1"
NUCLEUS = "H"
METHYL_ATOM_COUNT = 3

ATOM_COUNT_BASES: tuple[str, ...] = (
    "methyl_explicit",
    "methyl_integral",
    "total_h_constraint",
    "integral_ratio",
    "minimum_one",
)
METHYL_SOURCES: tuple[str, ...] = ("explicit", "integration")
OVERLAP_REGION_KINDS: tuple[str, ...] = ("ambiguous_split", "interleaved", "near_overlap")
UNCERTAINTY_REASONS: tuple[str, ...] = ("ambiguous_split", "interleaved", "near_overlap", "low_snr")

_INFEASIBLE_COST = 1.0e12


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------


def _require_finite_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ProtonProcessorOptions.{name} must be a number, got {value!r}")
    if not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(
            f"ProtonProcessorOptions.{name} must be finite and positive, got {value!r}"
        )


def _require_finite_non_negative(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"ProtonProcessorOptions.{name} must be a number, got {value!r}")
    if not math.isfinite(float(value)) or float(value) < 0:
        raise ValueError(
            f"ProtonProcessorOptions.{name} must be finite and non-negative, got {value!r}"
        )


@dataclass(frozen=True)
class ProtonProcessorOptions:
    """Processor parameters (all defaults sane, all bounded).

    Attributes:
        total_hydrogens: Structure-related total proton count; ``None`` leaves
            the total-H constraint unavailable (reported, never silent).
        methyl_line_indices: Explicit methyl annotations by
            :class:`~acp.nmr.models.SpectralLine` index (pins 3H).
        methyl_peak_indices: Explicit methyl annotations by
            :class:`~acp.nmr.models.ExperimentalPeak` index (fallback: the
            peak's position in the spectrum's peak list).
        max_coupling_hz: Upper bound on a first-order coupling constant (Hz);
            a segment whose fitted spacing exceeds it cannot be one multiplet.
        fallback_frequency_mhz: Spectrometer frequency assumed for Hz<->ppm
            conversion when the spectrum records no acquisition frequency.
        region_split_gap_ppm: Lines farther apart than this are processed as
            independent regions (bounded runtime; no multiplet spans it).
        ap_tolerance_ppm: Position tolerance for the arithmetic-progression
            (interleaved multiplet) extraction pass.
        min_residual_ppm: Position-model noise floor (ppm); keeps 2-line
            segments from getting a free zero-residual BIC.
        bic_penalty: Complexity charge per extra segment (in ``ln(N)`` units,
            BIC analogue of the upstream ``ln(N)*k`` term).
        ambiguity_margin: Alternative groupings with ``delta_bic`` below this
            mark the region unresolved (the data does not decide).
        overlap_window_ppm: Multiplet centers closer than this are reported
            as a near-overlap region.
        snr_threshold: Line S/N below this is flagged ``low_snr`` (in edge
            noise MAD units, matching the picker's threshold).
        intensity_mode_penalty: BIC charge per extra intensity local maximum
            inside a segment (a multiplet envelope is unimodal).
        width_mismatch_penalty: BIC charge for linewidth heterogeneity.
        width_ratio_max: Allowed max/min linewidth ratio inside a segment
            before the width penalty applies.
        methyl_integral_tolerance: |expected - 3| (H units) within which a
            group is recognised as a methyl by integration.
        max_region_lines: Hard bound on lines per region (runtime bound).
        max_alternatives: Maximum alternative groupings recorded per region.
        position_tolerance_ppm: Annotation<->line and multiplet<->peak
            position matching tolerance (metrics + capacity export).
    """

    total_hydrogens: int | None = None
    methyl_line_indices: tuple[int, ...] = ()
    methyl_peak_indices: tuple[int, ...] = ()
    max_coupling_hz: float = 20.0
    fallback_frequency_mhz: float = 500.0
    region_split_gap_ppm: float = 0.25
    ap_tolerance_ppm: float = 0.002
    min_residual_ppm: float = 0.001
    bic_penalty: float = 2.0
    ambiguity_margin: float = 1.5
    overlap_window_ppm: float = 0.03
    snr_threshold: float = 8.0
    intensity_mode_penalty: float = 2.0
    width_mismatch_penalty: float = 2.0
    width_ratio_max: float = 2.5
    methyl_integral_tolerance: float = 0.6
    max_region_lines: int = 16
    max_alternatives: int = 3
    position_tolerance_ppm: float = 0.05

    def __post_init__(self) -> None:
        if self.total_hydrogens is not None and (
            isinstance(self.total_hydrogens, bool)
            or not isinstance(self.total_hydrogens, int)
            or self.total_hydrogens < 1
        ):
            raise ValueError(
                f"ProtonProcessorOptions.total_hydrogens must be an int >= 1 or None, "
                f"got {self.total_hydrogens!r}"
            )
        for name in ("methyl_line_indices", "methyl_peak_indices"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in values
            ):
                raise ValueError(f"ProtonProcessorOptions.{name} must be a tuple of ints >= 0")
        _require_finite_positive("max_coupling_hz", self.max_coupling_hz)
        _require_finite_positive("fallback_frequency_mhz", self.fallback_frequency_mhz)
        _require_finite_positive("region_split_gap_ppm", self.region_split_gap_ppm)
        _require_finite_positive("ap_tolerance_ppm", self.ap_tolerance_ppm)
        _require_finite_positive("min_residual_ppm", self.min_residual_ppm)
        _require_finite_positive("bic_penalty", self.bic_penalty)
        _require_finite_non_negative("ambiguity_margin", self.ambiguity_margin)
        _require_finite_non_negative("overlap_window_ppm", self.overlap_window_ppm)
        _require_finite_positive("snr_threshold", self.snr_threshold)
        _require_finite_non_negative("intensity_mode_penalty", self.intensity_mode_penalty)
        _require_finite_non_negative("width_mismatch_penalty", self.width_mismatch_penalty)
        _require_finite_positive("width_ratio_max", self.width_ratio_max)
        if self.width_ratio_max < 1:
            raise ValueError("ProtonProcessorOptions.width_ratio_max must be >= 1")
        _require_finite_non_negative("methyl_integral_tolerance", self.methyl_integral_tolerance)
        if (
            isinstance(self.max_region_lines, bool)
            or not isinstance(self.max_region_lines, int)
            or self.max_region_lines < 2
        ):
            raise ValueError("ProtonProcessorOptions.max_region_lines must be an int >= 2")
        if (
            isinstance(self.max_alternatives, bool)
            or not isinstance(self.max_alternatives, int)
            or self.max_alternatives < 0
        ):
            raise ValueError("ProtonProcessorOptions.max_alternatives must be an int >= 0")
        _require_finite_positive("position_tolerance_ppm", self.position_tolerance_ppm)


# ---------------------------------------------------------------------------
# public records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtonGrouping:
    """One candidate grouping of a region's lines (primary or alternative).

    Attributes:
        grouping_id: Stable id (``R<region>.alt<rank>`` for alternatives).
        line_groups: Line source indices per multiplet, sorted by position.
        bic: BIC score (lower is better).
        delta_bic: ``bic - primary_bic`` (positive = worse than primary).
        kind: ``"contiguous"`` or ``"arithmetic_progression"``.
    """

    grouping_id: str
    line_groups: tuple[tuple[int, ...], ...]
    bic: float
    delta_bic: float
    kind: str

    def to_dict(self) -> dict[str, object]:
        return {
            "grouping_id": self.grouping_id,
            "line_groups": [list(group) for group in self.line_groups],
            "bic": self.bic,
            "delta_bic": self.delta_bic,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ProtonMultiplet:
    """One resolved multiplet with its constraint-aware atom capacity.

    Attributes:
        multiplet_id: Stable id (``M<rank>`` by position).
        center_ppm: Intensity-weighted line center.
        line_indices: Source line indices in this multiplet (position order).
        line_positions_ppm: Their positions.
        spacing_ppm: Fitted first-order spacing (``J`` in ppm), ``None`` for
            a single line.
        coupling_hz: ``spacing_ppm * frequency`` when a frequency is known.
        raw_integral: Sum of the member line integrals (arbitrary units).
        atom_count: Constraint-resolved atom capacity (>= 1).
        atom_count_basis: Why this count (closed vocabulary).
        methyl: Recognised 3H methyl group.
        methyl_source: ``"explicit"`` / ``"integration"`` / ``None``.
        overlap: Member of an overlap / ambiguity region.
        low_snr: Contains at least one low-SNR line (flagged, not dropped).
        slots: Number of signals that may share the region's peaks (>= 1).
        uncertainty_reasons: Closed vocabulary reasons.
        shape_rss: Position-model residual of the fitted multiplet.
    """

    multiplet_id: str
    center_ppm: float
    line_indices: tuple[int, ...]
    line_positions_ppm: tuple[float, ...]
    spacing_ppm: float | None
    coupling_hz: float | None
    raw_integral: float
    atom_count: int
    atom_count_basis: str
    methyl: bool
    methyl_source: str | None
    overlap: bool
    low_snr: bool
    slots: int
    uncertainty_reasons: tuple[str, ...]
    shape_rss: float

    def to_dict(self) -> dict[str, object]:
        return {
            "multiplet_id": self.multiplet_id,
            "center_ppm": self.center_ppm,
            "line_indices": list(self.line_indices),
            "line_positions_ppm": list(self.line_positions_ppm),
            "spacing_ppm": self.spacing_ppm,
            "coupling_hz": self.coupling_hz,
            "raw_integral": self.raw_integral,
            "atom_count": self.atom_count,
            "atom_count_basis": self.atom_count_basis,
            "methyl": self.methyl,
            "methyl_source": self.methyl_source,
            "overlap": self.overlap,
            "low_snr": self.low_snr,
            "slots": self.slots,
            "uncertainty_reasons": list(self.uncertainty_reasons),
            "shape_rss": self.shape_rss,
        }


@dataclass(frozen=True)
class ProtonOverlapRegion:
    """An overlapping / ambiguous ppm region with its alternative groupings.

    Attributes:
        region_id: Stable id (``R<rank>`` by position).
        low_ppm / high_ppm: Region bounds (involved multiplet line spans).
        multiplet_ids: Primary multiplets involved.
        kind: ``"ambiguous_split"`` (data undecided), ``"interleaved"``
            (line sets interleave), or ``"near_overlap"`` (centers close).
        resolved: The evidence decides the primary grouping.
        alternatives: Alternative groupings (delta_bic > 0, best first).
    """

    region_id: str
    low_ppm: float
    high_ppm: float
    multiplet_ids: tuple[str, ...]
    kind: str
    resolved: bool
    alternatives: tuple[ProtonGrouping, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "region_id": self.region_id,
            "low_ppm": self.low_ppm,
            "high_ppm": self.high_ppm,
            "multiplet_ids": list(self.multiplet_ids),
            "kind": self.kind,
            "resolved": self.resolved,
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
        }


@dataclass(frozen=True)
class ProtonConstraintReport:
    """How the total-H / methyl constraints resolved (never silent).

    Attributes:
        total_hydrogens: Requested structure-related total (``None`` = absent).
        total_hydrogens_resolved: Sum of the resolved atom counts.
        total_constraint_applied: The sum matches ``total_hydrogens`` and no
            conflict was recorded.
        methyl_multiplet_ids: Multiplets recognised as methyls.
        conflicts: Typed conflicts (closed, human-readable tags).
        notes: Constraint facts, including the unavailable-total note.
    """

    total_hydrogens: int | None
    total_hydrogens_resolved: int
    total_constraint_applied: bool
    methyl_multiplet_ids: tuple[str, ...]
    conflicts: tuple[str, ...]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "total_hydrogens": self.total_hydrogens,
            "total_hydrogens_resolved": self.total_hydrogens_resolved,
            "total_constraint_applied": self.total_constraint_applied,
            "methyl_multiplet_ids": list(self.methyl_multiplet_ids),
            "conflicts": list(self.conflicts),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class ProtonPeakCapacity:
    """Per-multiplet capacity export consumable by the assignment layer.

    ``index`` follows the ``iterative_assignment`` peak-index convention: the
    matched ``ExperimentalPeak.index`` when set, else the peak's position in
    the spectrum's peak list (the same fallback the assignment solver uses).
    """

    multiplet_id: str
    element: str
    index: int | None
    atom_capacity: int
    slots: int
    center_ppm: float
    raw_integral: float

    def to_dict(self) -> dict[str, object]:
        return {
            "multiplet_id": self.multiplet_id,
            "element": self.element,
            "index": self.index,
            "atom_capacity": self.atom_capacity,
            "slots": self.slots,
            "center_ppm": self.center_ppm,
            "raw_integral": self.raw_integral,
        }

    def to_peak_constraint(self, *, index_override: int | None = None):
        """Build the todo-46 ``PeakConstraint`` for this capacity.

        The assignment module is consumed read-only (never edited here); the
        import is local so the processor stays importable without it.
        """
        from acp.nmr.iterative_assignment import PeakConstraint  # noqa: PLC0415

        index = self.index if index_override is None else index_override
        return PeakConstraint(
            element=self.element,
            index=index,
            atom_capacity=self.atom_capacity,
            slots=self.slots,
        )


@dataclass(frozen=True)
class ProtonProcessingResult:
    """Processor outcome: multiplets + regions + constraint report + exports."""

    processor_id: str
    nucleus: str
    source_dir: str
    frequency_mhz: float | None
    multiplets: tuple[ProtonMultiplet, ...]
    overlap_regions: tuple[ProtonOverlapRegion, ...]
    constraints: ProtonConstraintReport
    capacity_records: tuple[ProtonPeakCapacity, ...]
    unassigned_line_indices: tuple[int, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "processor_id": self.processor_id,
            "nucleus": self.nucleus,
            "source_dir": self.source_dir,
            "frequency_mhz": self.frequency_mhz,
            "multiplets": [multiplet.to_dict() for multiplet in self.multiplets],
            "overlap_regions": [region.to_dict() for region in self.overlap_regions],
            "constraints": self.constraints.to_dict(),
            "capacity_records": [record.to_dict() for record in self.capacity_records],
            "unassigned_line_indices": list(self.unassigned_line_indices),
            "warnings": list(self.warnings),
        }

    def capacities(self) -> tuple[ProtonPeakCapacity, ...]:
        """Per-multiplet capacity export in multiplet order."""
        return self.capacity_records


@dataclass(frozen=True)
class ProtonAnnotation:
    """Manual ground truth for one multiplet (metrics comparison)."""

    center_ppm: float
    line_positions_ppm: tuple[float, ...] = ()
    atom_count: int | None = None
    label: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "center_ppm": self.center_ppm,
            "line_positions_ppm": list(self.line_positions_ppm),
            "atom_count": self.atom_count,
            "label": self.label,
        }


@dataclass(frozen=True)
class ProtonGroupingMetrics:
    """Grouping accuracy / precision-recall against manual annotations."""

    n_predicted: int
    n_annotated: int
    matched: int
    precision: float
    recall: float
    f1: float
    pairwise_accuracy: float | None
    atom_count_exact: int
    atom_count_compared: int
    atom_count_accuracy: float | None
    unmatched_predicted: tuple[str, ...]
    unmatched_annotated: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "n_predicted": self.n_predicted,
            "n_annotated": self.n_annotated,
            "matched": self.matched,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "pairwise_accuracy": self.pairwise_accuracy,
            "atom_count_exact": self.atom_count_exact,
            "atom_count_compared": self.atom_count_compared,
            "atom_count_accuracy": self.atom_count_accuracy,
            "unmatched_predicted": list(self.unmatched_predicted),
            "unmatched_annotated": list(self.unmatched_annotated),
        }


# ---------------------------------------------------------------------------
# internal model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Line:
    index: int
    position_ppm: float
    intensity: float
    width_hz: float | None
    integral: float
    snr: float | None
    low_snr: bool


@dataclass(frozen=True)
class _SegmentFit:
    cost: float
    rss: float
    spacing_ppm: float | None
    feasible: bool


@dataclass(frozen=True)
class _Partition:
    groups: tuple[tuple[int, ...], ...]  # positions within the partitioned list
    cost: float
    bic: float
    kind: str


@dataclass
class _Group:
    """Mutable intermediate multiplet before constraints/flags resolve."""

    region_positions: tuple[int, ...]
    line_indices: tuple[int, ...]
    line_positions_ppm: tuple[float, ...]
    center_ppm: float
    spacing_ppm: float | None
    coupling_hz: float | None
    raw_integral: float
    shape_rss: float
    low_snr: bool
    capacity_index: int | None = None
    methyl: bool = False
    methyl_source: str | None = None
    atom_count: int = 1
    atom_count_basis: str = "minimum_one"
    overlap: bool = False
    slots: int = 1
    uncertainty_reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# line extraction / region splitting / fitting
# ---------------------------------------------------------------------------


def _extract_lines(spectrum: ProcessedSpectrum, options: ProtonProcessorOptions) -> list[_Line]:
    """Layer-2 lines (fallback: peaks) as internal line records."""
    noise = float(spectrum.noise) if spectrum.noise else 0.0

    def snr_of(intensity: float) -> float | None:
        return intensity / noise if noise > 0 else None

    lines: list[_Line] = []
    if spectrum.lines:
        for position, line in enumerate(spectrum.lines):
            index = line.index if line.index is not None else position
            intensity = float(line.intensity)
            integral = float(line.integral) if line.integral is not None else max(intensity, 0.0)
            snr = snr_of(intensity)
            lines.append(
                _Line(
                    index=int(index),
                    position_ppm=float(line.position_ppm),
                    intensity=intensity,
                    width_hz=line.width_hz,
                    integral=integral,
                    snr=snr,
                    low_snr=snr is not None and snr < options.snr_threshold,
                )
            )
        return lines

    for position, peak in enumerate(spectrum.peaks):
        index = peak.index if peak.index is not None else position
        multiplicity = float(max(1, int(peak.multiplicity)))
        snr = snr_of(multiplicity)
        lines.append(
            _Line(
                index=int(index),
                position_ppm=float(peak.shift_ppm),
                intensity=multiplicity,
                width_hz=None,
                integral=multiplicity,
                snr=snr,
                low_snr=snr is not None and snr < options.snr_threshold,
            )
        )
    return lines


def _split_into_regions(
    lines: Sequence[_Line], options: ProtonProcessorOptions
) -> list[list[_Line]]:
    """Split sorted lines into independent regions (gap + runtime bounds)."""
    if not lines:
        return []
    chunks: list[list[_Line]] = [[lines[0]]]
    for line in lines[1:]:
        if line.position_ppm - chunks[-1][-1].position_ppm > options.region_split_gap_ppm:
            chunks.append([line])
        else:
            chunks[-1].append(line)

    bounded: list[list[_Line]] = []
    for chunk in chunks:
        while len(chunk) > options.max_region_lines:
            gaps = [
                (chunk[position + 1].position_ppm - chunk[position].position_ppm, position)
                for position in range(len(chunk) - 1)
            ]
            _, split_at = max(gaps, key=lambda item: (item[0], -item[1]))
            bounded.append(chunk[: split_at + 1])
            chunk = chunk[split_at + 1 :]
        bounded.append(chunk)
    return bounded


def _linear_fit(positions: Sequence[float]) -> tuple[float, float | None]:
    """Least-squares line fit over equally indexed positions -> (rss, slope)."""
    count = len(positions)
    if count < 2:
        return 0.0, None
    mean_x = (count - 1) / 2.0
    mean_y = sum(positions) / count
    sxx = sum((index - mean_x) ** 2 for index in range(count))
    sxy = sum((index - mean_x) * (positions[index] - mean_y) for index in range(count))
    if sxx <= 0:
        return 0.0, None
    slope = sxy / sxx
    intercept = mean_y - slope * mean_x
    rss = sum((positions[index] - (intercept + slope * index)) ** 2 for index in range(count))
    return rss, slope


def _count_intensity_modes(values: Sequence[float]) -> int:
    """Local maxima of the intensity envelope (plateaus compressed)."""
    compressed = [
        value for index, value in enumerate(values) if index == 0 or value != values[index - 1]
    ]
    if len(compressed) < 3:
        return 1
    modes = 0
    for index, value in enumerate(compressed):
        left = compressed[index - 1] if index > 0 else None
        right = compressed[index + 1] if index + 1 < len(compressed) else None
        if (left is None or value > left) and (right is None or value > right):
            modes += 1
    return max(modes, 1)


def _fit_segment(
    member_lines: Sequence[_Line], options: ProtonProcessorOptions, j_max_ppm: float
) -> _SegmentFit:
    """BIC segment cost of one candidate multiplet over its member lines."""
    positions = [line.position_ppm for line in member_lines]
    count = len(positions)
    rss, slope = _linear_fit(positions)
    feasible = slope is None or 0.0 < slope <= j_max_ppm
    floor_sq = options.min_residual_ppm**2
    cost = count * math.log(rss / count + floor_sq)
    widths = [line.width_hz for line in member_lines if line.width_hz is not None]
    if len(widths) == count and count >= 2:
        smallest = min(widths)
        if smallest > 0:
            ratio = max(widths) / smallest
            if ratio > options.width_ratio_max:
                cost += options.width_mismatch_penalty * (
                    math.log(ratio) - math.log(options.width_ratio_max)
                )
    if count >= 3:
        modes = _count_intensity_modes([line.intensity for line in member_lines])
        cost += options.intensity_mode_penalty * max(0, modes - 1)
    if not feasible:
        cost = _INFEASIBLE_COST
    return _SegmentFit(cost=cost, rss=rss, spacing_ppm=slope, feasible=feasible)


def _partition_bic(
    cost: float, segments: int, region_lines: int, options: ProtonProcessorOptions
) -> float:
    """BIC = total segment cost + ``bic_penalty * ln(N) * segments``."""
    return cost + options.bic_penalty * math.log(max(region_lines, 2)) * segments


def _contiguous_partitions(
    lines: Sequence[_Line], options: ProtonProcessorOptions, j_max_ppm: float
) -> list[_Partition]:
    """All contiguous k-segmentations of *lines*, best BIC per k.

    The DP keeps a deterministic tie-break (larger split index first), so
    equal-BIC partitions resolve the same way on every run.
    """
    count = len(lines)
    fits: list[list[_SegmentFit | None]] = [[None] * count for _ in range(count)]
    for start in range(count):
        for end in range(start, count):
            fits[start][end] = _fit_segment(list(lines[start : end + 1]), options, j_max_ppm)

    infinity = float("inf")
    dp = [[infinity] * count for _ in range(count + 1)]
    back = [[-1] * count for _ in range(count + 1)]
    for end in range(count):
        fit = fits[0][end]
        assert fit is not None
        dp[1][end] = fit.cost
    for segments in range(2, count + 1):
        for end in range(segments - 1, count):
            best = infinity
            best_split = -1
            for split in range(segments - 2, end):
                previous = dp[segments - 1][split]
                if previous == infinity:
                    continue
                fit = fits[split + 1][end]
                assert fit is not None
                value = previous + fit.cost
                if value < best - 1e-12 or (abs(value - best) <= 1e-12 and split > best_split):
                    best = value
                    best_split = split
            dp[segments][end] = best
            back[segments][end] = best_split

    partitions: list[_Partition] = []
    for segments in range(1, count + 1):
        cost = dp[segments][count - 1]
        if cost == infinity:
            continue
        groups: list[tuple[int, ...]] = []
        end = count - 1
        for level in range(segments, 0, -1):
            split = back[level][end]
            start = 0 if split < 0 else split + 1
            groups.append(tuple(range(start, end + 1)))
            end = split
        groups.reverse()
        partitions.append(
            _Partition(
                groups=tuple(groups),
                cost=cost,
                bic=_partition_bic(cost, segments, count, options),
                kind="contiguous",
            )
        )
    partitions.sort(
        key=lambda partition: (round(partition.bic, 9), len(partition.groups), partition.groups)
    )
    return partitions


def _lattice_classes(
    positions: Sequence[float], step: float, tolerance: float
) -> list[tuple[int, ...]]:
    """Greedy lattice classes: lines within *tolerance* of ``anchor + k*step``."""
    assigned = [False] * len(positions)
    classes: list[tuple[int, ...]] = []
    for start in range(len(positions)):
        if assigned[start]:
            continue
        anchor = positions[start]
        members = [start]
        assigned[start] = True
        for other in range(start + 1, len(positions)):
            if assigned[other]:
                continue
            multiple = round((positions[other] - anchor) / step)
            if multiple >= 1 and abs(positions[other] - (anchor + multiple * step)) <= tolerance:
                members.append(other)
                assigned[other] = True
        classes.append(tuple(members))
    return classes


def _arithmetic_progression_partitions(
    lines: Sequence[_Line], options: ProtonProcessorOptions, j_max_ppm: float
) -> list[_Partition]:
    """Interleaved-multiplet candidates: lattice extraction + remainder DP."""
    count = len(lines)
    if count < 3:
        return []
    positions = [line.position_ppm for line in lines]
    tolerance = options.ap_tolerance_ppm

    gaps: list[float] = []
    for first in range(count):
        for second in range(first + 1, count):
            gap = positions[second] - positions[first]
            if 0 < gap <= j_max_ppm + tolerance:
                gaps.append(gap)

    clusters: list[list[float]] = []
    for gap in sorted(gaps):
        for cluster in clusters:
            if abs(gap - cluster[0]) <= tolerance:
                cluster.append(gap)
                break
        else:
            clusters.append([gap])
    steps = sorted(
        ((len(cluster), cluster[0]) for cluster in clusters), key=lambda item: (-item[0], item[1])
    )

    partitions: list[_Partition] = []
    seen: set[tuple[tuple[int, ...], ...]] = set()
    for support, step in steps[:6]:
        if support < 3:
            continue
        classes = _lattice_classes(positions, step, tolerance)
        lattice_groups = [members for members in classes if len(members) >= 3]
        if not lattice_groups:
            continue
        assigned = {member for members in lattice_groups for member in members}
        leftovers = [index for index in range(count) if index not in assigned]
        remainder_groups: list[tuple[int, ...]] = []
        if leftovers:
            remainder_lines = [lines[index] for index in leftovers]
            remainder_partitions = _contiguous_partitions(remainder_lines, options, j_max_ppm)
            if remainder_partitions:
                remainder_groups = [
                    tuple(leftovers[position] for position in group)
                    for group in remainder_partitions[0].groups
                ]
        groups = tuple(
            sorted([tuple(members) for members in lattice_groups] + remainder_groups, key=min)
        )
        key = tuple(sorted(groups))
        if key in seen:
            continue
        seen.add(key)
        cost = 0.0
        for group in groups:
            cost += _fit_segment([lines[index] for index in group], options, j_max_ppm).cost
        partitions.append(
            _Partition(
                groups=groups,
                cost=cost,
                bic=_partition_bic(cost, len(groups), count, options),
                kind="arithmetic_progression",
            )
        )
    partitions.sort(
        key=lambda partition: (round(partition.bic, 9), len(partition.groups), partition.groups)
    )
    return partitions


def _dedupe_partitions(partitions: Sequence[_Partition]) -> list[_Partition]:
    deduped: list[_Partition] = []
    seen: set[tuple[tuple[int, ...], ...]] = set()
    for partition in partitions:
        key = tuple(sorted(partition.groups))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(partition)
    return deduped


def _weighted_center(lines: Sequence[_Line], positions: Sequence[int]) -> float:
    weights = [max(lines[index].intensity, 0.0) for index in positions]
    total = sum(weights)
    if total <= 0:
        return sum(lines[index].position_ppm for index in positions) / len(positions)
    return (
        sum(weight * lines[index].position_ppm for weight, index in zip(weights, positions)) / total
    )


# ---------------------------------------------------------------------------
# constraints (methyl recognition + integer total-H allocation)
# ---------------------------------------------------------------------------


def _expected_counts(
    groups: Sequence[_Group], options: ProtonProcessorOptions
) -> tuple[list[float], bool, str | None]:
    """Expected H count per group; returns ``(expected, from_total, note)``."""
    total_hydrogens = options.total_hydrogens
    total_raw = sum(group.raw_integral for group in groups)
    if total_hydrogens is not None:
        if total_raw > 0:
            scale = total_hydrogens / total_raw
            return [group.raw_integral * scale for group in groups], True, None
        return [1.0] * len(groups), True, "total_hydrogens_given_but_no_integral"
    positive = [group.raw_integral for group in groups if group.raw_integral > 0]
    unit = min(positive) if positive else 1.0
    if unit <= 0:
        unit = 1.0
    return [group.raw_integral / unit for group in groups], False, None


def _recognise_methyls(
    groups: Sequence[_Group],
    expected: Sequence[float],
    options: ProtonProcessorOptions,
    explicit_line: set[int],
    explicit_peak: set[int],
) -> None:
    """Flag methyls (explicit annotation or integration ~= 3H)."""
    for position, group in enumerate(groups):
        explicit = any(index in explicit_line for index in group.line_indices) or (
            group.capacity_index is not None and group.capacity_index in explicit_peak
        )
        if explicit:
            group.methyl = True
            group.methyl_source = "explicit"
            continue
        if abs(expected[position] - METHYL_ATOM_COUNT) <= options.methyl_integral_tolerance:
            group.methyl = True
            group.methyl_source = "integration"


def _allocate_atom_counts(
    groups: Sequence[_Group],
    options: ProtonProcessorOptions,
    total_available: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Integer atom counts; exact total when the constraint is available."""
    count = len(groups)
    conflicts: list[str] = []
    notes: list[str] = []
    if not total_available:
        positive = [group.raw_integral for group in groups if group.raw_integral > 0]
        unit = min(positive) if positive else 1.0
        if unit <= 0:
            unit = 1.0
        for group in groups:
            ratio = group.raw_integral / unit
            group.atom_count = max(1, int(round(ratio)))
            group.atom_count_basis = "integral_ratio"
            if group.methyl:
                group.atom_count = METHYL_ATOM_COUNT
                group.atom_count_basis = f"methyl_{group.methyl_source}"
        return tuple(conflicts), tuple(notes)

    total_hydrogens = int(options.total_hydrogens or 0)
    methyl_positions = [position for position, group in enumerate(groups) if group.methyl]
    minimum_needed = METHYL_ATOM_COUNT * len(methyl_positions) + (count - len(methyl_positions))
    if methyl_positions and total_hydrogens < minimum_needed:
        conflicts.append("methyl_pins_exceed_total_hydrogens")
        notes.append(
            f"pinned methyls need {minimum_needed} H but total_hydrogens={total_hydrogens}; "
            "falling back to proportional allocation"
        )
        methyl_positions = []
    if total_hydrogens < count:
        conflicts.append("total_hydrogens_below_group_count")
        notes.append(
            f"total_hydrogens={total_hydrogens} < {count} multiplets; each multiplet keeps >= 1 H"
        )

    counts = [0] * count
    for position in methyl_positions:
        counts[position] = METHYL_ATOM_COUNT
        groups[position].atom_count_basis = f"methyl_{groups[position].methyl_source}"
    methyl_set = set(methyl_positions)
    unpinned = [position for position in range(count) if position not in methyl_set]
    remaining_budget = total_hydrogens - METHYL_ATOM_COUNT * len(methyl_positions)

    if not unpinned:
        if remaining_budget > 0:
            conflicts.append("unallocated_hydrogens")
            notes.append(
                f"{remaining_budget} H cannot be allocated: every multiplet is a pinned methyl"
            )
        for position, group in enumerate(groups):
            group.atom_count = counts[position]
        return tuple(conflicts), tuple(notes)

    raw_unpinned = [max(groups[position].raw_integral, 0.0) for position in unpinned]
    raw_total = sum(raw_unpinned)
    budget = max(remaining_budget, len(unpinned))
    if raw_total > 0:
        ideals = [budget * raw / raw_total for raw in raw_unpinned]
    else:
        ideals = [budget / len(unpinned)] * len(unpinned)

    base = [max(1, math.floor(ideal)) for ideal in ideals]
    assigned = sum(base)
    while assigned > budget:
        candidates = [position for position, value in enumerate(base) if value > 1]
        if not candidates:
            break
        position = min(candidates, key=lambda item: (ideals[item] - base[item], item))
        base[position] -= 1
        assigned -= 1
    while assigned < budget:
        position = max(range(len(base)), key=lambda item: (ideals[item] - base[item], -item))
        base[position] += 1
        assigned += 1

    for offset, position in enumerate(unpinned):
        counts[position] = base[offset]
        groups[position].atom_count_basis = "total_h_constraint"
    for position, group in enumerate(groups):
        group.atom_count = counts[position]
    return tuple(conflicts), tuple(notes)


# ---------------------------------------------------------------------------
# overlap detection helpers
# ---------------------------------------------------------------------------


def _groups_interleave(groups: Sequence[tuple[int, ...]]) -> bool:
    for first in range(len(groups)):
        for second in range(first + 1, len(groups)):
            a_min, a_max = min(groups[first]), max(groups[first])
            b_min, b_max = min(groups[second]), max(groups[second])
            if a_min < b_max and b_min < a_max:
                return True
    return False


def _centers_close(centers: Sequence[float], window_ppm: float) -> bool:
    for first in range(len(centers)):
        for second in range(first + 1, len(centers)):
            if abs(centers[first] - centers[second]) <= window_ppm:
                return True
    return False


def _representative_peak_index(
    member_lines: Sequence[_Line], peaks: Sequence[object], tolerance_ppm: float
) -> int | None:
    """Peak index of the multiplet's tallest line (fallback: list position)."""
    if not peaks:
        return None
    for line in sorted(member_lines, key=lambda item: (-item.intensity, item.position_ppm)):
        matches: list[tuple[float, int]] = []
        for position, peak in enumerate(peaks):
            peak_position = float(getattr(peak, "shift_ppm"))
            distance = abs(line.position_ppm - peak_position)
            if distance <= tolerance_ppm:
                peak_index = getattr(peak, "index")
                resolved = int(peak_index) if peak_index is not None else position
                matches.append((distance, resolved))
        if matches:
            return min(matches, key=lambda item: (item[0], item[1]))[1]


# ---------------------------------------------------------------------------
# main entry point
# ---------------------------------------------------------------------------


def process_proton_spectrum(
    spectrum: ProcessedSpectrum,
    options: ProtonProcessorOptions | None = None,
) -> ProtonProcessingResult:
    """Group a processed proton spectrum into constrained multiplets.

    Args:
        spectrum: Layer-2 processed spectrum (``element == "H"``, formal
            usable).  Failed spectra are rejected -- the formal path never
            feeds them here (todo 42).
        options: Processor parameters (defaults: total-H absent, 20 Hz
            coupling bound, 500 MHz fallback frequency).

    Raises:
        ValueError: When the spectrum is not a formal-usable proton spectrum,
            or carries neither lines nor peaks.

    Returns:
        :class:`ProtonProcessingResult` with multiplets, overlap regions,
        the constraint report and the peak-capacity export.
    """
    options = options or ProtonProcessorOptions()
    element = normalize_symbol(spectrum.element)
    if element != NUCLEUS:
        raise ValueError(
            f"proton processor requires element {NUCLEUS!r}, got {spectrum.element!r} "
            f"(nucleus {spectrum.nucleus!r})"
        )
    if spectrum.assessment is not None and spectrum.assessment.status == "failed":
        raise ValueError(
            "proton processor refuses a spectrum that failed the processing gate "
            f"({', '.join(spectrum.assessment.reasons)}); the formal path excludes it"
        )
    if not spectrum.lines and not spectrum.peaks:
        raise ValueError(
            "proton processor needs picked lines or peaks; the spectrum carries neither "
            "(check peak picking / SNR)"
        )

    frequency_mhz: float | None = None
    if spectrum.acquisition is not None and spectrum.acquisition.frequency_mhz:
        frequency_mhz = float(spectrum.acquisition.frequency_mhz)
    elif options.fallback_frequency_mhz:
        frequency_mhz = float(options.fallback_frequency_mhz)
    j_max_ppm = options.max_coupling_hz / (frequency_mhz or options.fallback_frequency_mhz)

    raw_lines = _extract_lines(spectrum, options)
    sorted_lines = sorted(raw_lines, key=lambda line: (line.position_ppm, line.index))
    regions = _split_into_regions(sorted_lines, options)

    # --- per-region model selection --------------------------------------
    region_partitions: list[list[_Partition]] = []
    for region in regions:
        candidates = _dedupe_partitions(
            _contiguous_partitions(region, options, j_max_ppm)
            + _arithmetic_progression_partitions(region, options, j_max_ppm)
        )
        candidates.sort(
            key=lambda partition: (round(partition.bic, 9), len(partition.groups), partition.groups)
        )
        region_partitions.append(candidates)

    # --- build intermediate groups ---------------------------------------
    groups: list[_Group] = []
    region_group_lookup: list[dict[tuple[int, ...], _Group]] = []
    for region_index, region in enumerate(regions):
        primary = region_partitions[region_index][0]
        lookup: dict[tuple[int, ...], _Group] = {}
        for positions in primary.groups:
            member_positions = tuple(sorted(positions))
            member_lines = [region[position] for position in member_positions]
            fit = _fit_segment(member_lines, options, j_max_ppm)
            spacing = fit.spacing_ppm if len(member_positions) >= 2 and fit.feasible else None
            low_snr = any(line.low_snr for line in member_lines)
            group = _Group(
                region_positions=member_positions,
                line_indices=tuple(line.index for line in member_lines),
                line_positions_ppm=tuple(line.position_ppm for line in member_lines),
                center_ppm=_weighted_center(region, member_positions),
                spacing_ppm=spacing,
                coupling_hz=(spacing * frequency_mhz)
                if spacing is not None and frequency_mhz
                else None,
                raw_integral=float(sum(line.integral for line in member_lines)),
                shape_rss=fit.rss,
                low_snr=low_snr,
                capacity_index=_representative_peak_index(
                    member_lines, spectrum.peaks, options.position_tolerance_ppm
                ),
                uncertainty_reasons=("low_snr",) if low_snr else (),
            )
            lookup[member_positions] = group
            groups.append(group)
        region_group_lookup.append(lookup)

    groups.sort(key=lambda group: (group.center_ppm, group.line_indices))

    # --- constraint resolution -------------------------------------------
    expected, total_available, expected_note = _expected_counts(groups, options)
    _recognise_methyls(
        groups,
        expected,
        options,
        set(options.methyl_line_indices),
        set(options.methyl_peak_indices),
    )
    conflicts, allocation_notes = _allocate_atom_counts(groups, options, total_available)
    notes: list[str] = []
    if expected_note:
        notes.append(expected_note)
    notes.extend(allocation_notes)
    if not total_available:
        notes.append(
            "total_hydrogens not provided: total-H constraint unavailable; atom counts "
            "fall back to integral ratios (methyls pinned to 3H)"
        )
    if conflicts and any(group.methyl for group in groups):
        notes.append("methyl recognition is kept even where the allocation conflicted")

    # --- overlap regions (before freezing multiplets) --------------------
    pending_regions: list[
        tuple[str, float, float, tuple[_Group, ...], str, bool, tuple[ProtonGrouping, ...]]
    ] = []
    for region_index, region in enumerate(regions):
        candidates = region_partitions[region_index]
        primary = candidates[0]
        alternatives = [
            partition
            for partition in candidates[1:]
            if tuple(sorted(partition.groups)) != tuple(sorted(primary.groups))
        ][: options.max_alternatives]
        involved = [
            region_group_lookup[region_index][tuple(sorted(positions))]
            for positions in primary.groups
        ]
        best_delta = alternatives[0].bic - primary.bic if alternatives else None
        ambiguous = best_delta is not None and best_delta <= options.ambiguity_margin
        if ambiguous:
            kind = "ambiguous_split"
        elif _groups_interleave(list(primary.groups)):
            kind = "interleaved"
        else:
            centers = [group.center_ppm for group in involved]
            kind = "near_overlap" if _centers_close(centers, options.overlap_window_ppm) else None
        if kind is None:
            continue
        low_ppm = min(position for group in involved for position in group.line_positions_ppm)
        high_ppm = max(position for group in involved for position in group.line_positions_ppm)
        region_id = f"R{len(pending_regions) + 1}"
        alternatives_out = tuple(
            ProtonGrouping(
                grouping_id=f"{region_id}.alt{rank}",
                line_groups=tuple(
                    tuple(region[position].index for position in sorted(group))
                    for group in alternative.groups
                ),
                bic=alternative.bic,
                delta_bic=alternative.bic - primary.bic,
                kind=alternative.kind,
            )
            for rank, alternative in enumerate(alternatives, start=1)
        )
        pending_regions.append(
            (region_id, low_ppm, high_ppm, tuple(involved), kind, not ambiguous, alternatives_out)
        )
        for group in involved:
            group.overlap = True
            reasons = list(group.uncertainty_reasons)
            if kind not in reasons:
                reasons.append(kind)
            group.uncertainty_reasons = tuple(reasons)
            if ambiguous:
                group.slots = 2

    # --- freeze multiplets + capacity records -----------------------------
    multiplets = tuple(
        ProtonMultiplet(
            multiplet_id=f"M{rank}",
            center_ppm=group.center_ppm,
            line_indices=group.line_indices,
            line_positions_ppm=group.line_positions_ppm,
            spacing_ppm=group.spacing_ppm,
            coupling_hz=group.coupling_hz,
            raw_integral=group.raw_integral,
            atom_count=group.atom_count,
            atom_count_basis=group.atom_count_basis,
            methyl=group.methyl,
            methyl_source=group.methyl_source,
            overlap=group.overlap,
            low_snr=group.low_snr,
            slots=group.slots,
            uncertainty_reasons=group.uncertainty_reasons,
            shape_rss=group.shape_rss,
        )
        for rank, group in enumerate(groups, start=1)
    )
    rank_of = {id(group): rank for rank, group in enumerate(groups, start=1)}
    regions_out = tuple(
        ProtonOverlapRegion(
            region_id=region_id,
            low_ppm=low_ppm,
            high_ppm=high_ppm,
            multiplet_ids=tuple(f"M{rank_of[id(group)]}" for group in involved),
            kind=kind,
            resolved=resolved,
            alternatives=alternatives,
        )
        for region_id, low_ppm, high_ppm, involved, kind, resolved, alternatives in pending_regions
    )
    capacities = tuple(
        ProtonPeakCapacity(
            multiplet_id=multiplet.multiplet_id,
            element=NUCLEUS,
            index=groups[rank - 1].capacity_index,
            atom_capacity=multiplet.atom_count,
            slots=multiplet.slots,
            center_ppm=multiplet.center_ppm,
            raw_integral=multiplet.raw_integral,
        )
        for rank, multiplet in enumerate(multiplets, start=1)
    )

    assigned_line_indices = {index for multiplet in multiplets for index in multiplet.line_indices}
    unassigned = tuple(
        sorted(line.index for line in sorted_lines if line.index not in assigned_line_indices)
    )
    warnings: list[str] = []
    if any(multiplet.low_snr for multiplet in multiplets):
        warnings.append("low_snr_lines_present")
    warnings.extend(conflicts)

    return ProtonProcessingResult(
        processor_id=PROCESSOR_ID,
        nucleus=NUCLEUS,
        source_dir=spectrum.source_dir,
        frequency_mhz=frequency_mhz,
        multiplets=multiplets,
        overlap_regions=regions_out,
        constraints=ProtonConstraintReport(
            total_hydrogens=options.total_hydrogens,
            total_hydrogens_resolved=sum(multiplet.atom_count for multiplet in multiplets),
            total_constraint_applied=(
                options.total_hydrogens is not None
                and sum(multiplet.atom_count for multiplet in multiplets) == options.total_hydrogens
                and not conflicts
            ),
            methyl_multiplet_ids=tuple(
                multiplet.multiplet_id for multiplet in multiplets if multiplet.methyl
            ),
            conflicts=tuple(conflicts),
            notes=tuple(notes),
        ),
        capacity_records=capacities,
        unassigned_line_indices=unassigned,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# comparison metrics
# ---------------------------------------------------------------------------


def _match_annotations(
    result: ProtonProcessingResult,
    annotations: Sequence[ProtonAnnotation],
    tolerance_ppm: float,
) -> tuple[list[tuple[int, int]], list[tuple[int, ...]]]:
    """Greedy max-overlap matching -> ``(matched pairs, annotation line ids)``."""
    all_lines: list[tuple[int, float]] = [
        (index, position)
        for multiplet in result.multiplets
        for index, position in zip(multiplet.line_indices, multiplet.line_positions_ppm)
    ]
    annotation_ids: list[tuple[int, ...]] = []
    for annotation in annotations:
        ids: list[int] = []
        for position in annotation.line_positions_ppm:
            candidates = [
                (abs(position - line_position), index) for index, line_position in all_lines
            ]
            if not candidates:
                continue
            distance, index = min(candidates, key=lambda item: (item[0], item[1]))
            if distance <= tolerance_ppm:
                ids.append(index)
        annotation_ids.append(tuple(sorted(set(ids))))

    scores: list[tuple[int, int, int]] = []
    for predicted_rank, multiplet in enumerate(result.multiplets):
        predicted = set(multiplet.line_indices)
        for annotation_rank, ids in enumerate(annotation_ids):
            if ids:
                overlap = len(predicted.intersection(ids))
            else:
                overlap = (
                    1
                    if abs(multiplet.center_ppm - annotations[annotation_rank].center_ppm)
                    <= tolerance_ppm
                    else 0
                )
            if overlap > 0:
                scores.append((-overlap, predicted_rank, annotation_rank))
    scores.sort()
    matched: list[tuple[int, int]] = []
    used_predicted: set[int] = set()
    used_annotated: set[int] = set()
    for _, predicted_rank, annotation_rank in scores:
        if predicted_rank in used_predicted or annotation_rank in used_annotated:
            continue
        used_predicted.add(predicted_rank)
        used_annotated.add(annotation_rank)
        matched.append((predicted_rank, annotation_rank))
    return matched, annotation_ids


def compare_proton_grouping(
    result: ProtonProcessingResult,
    annotations: Sequence[ProtonAnnotation],
    *,
    position_tolerance_ppm: float = 0.05,
) -> ProtonGroupingMetrics:
    """Compare predicted multiplets against manual annotations.

    Matching is by maximum line overlap when annotations carry line positions
    (falling back to center distance otherwise).  ``pairwise_accuracy`` is the
    co-grouping agreement over all shared line pairs (Rand-style); it is
    ``None`` when the annotations carry no line positions.
    """
    matched_pairs, annotation_ids = _match_annotations(result, annotations, position_tolerance_ppm)
    n_predicted = len(result.multiplets)
    n_annotated = len(annotations)
    matched = len(matched_pairs)
    precision = matched / n_predicted if n_predicted else 0.0
    recall = matched / n_annotated if n_annotated else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0

    predicted_line_to_group = {
        index: rank
        for rank, multiplet in enumerate(result.multiplets)
        for index in multiplet.line_indices
    }
    annotation_line_to_group = {
        index: rank for rank, ids in enumerate(annotation_ids) for index in ids
    }
    pairwise_accuracy: float | None = None
    shared_lines = sorted(set(predicted_line_to_group).intersection(annotation_line_to_group))
    if len(shared_lines) >= 2:
        agreements = 0
        total = 0
        for first in range(len(shared_lines)):
            for second in range(first + 1, len(shared_lines)):
                a, b = shared_lines[first], shared_lines[second]
                same_predicted = predicted_line_to_group[a] == predicted_line_to_group[b]
                same_annotated = annotation_line_to_group[a] == annotation_line_to_group[b]
                total += 1
                if same_predicted == same_annotated:
                    agreements += 1
        pairwise_accuracy = agreements / total if total else None

    atom_count_exact = 0
    atom_count_compared = 0
    for predicted_rank, annotation_rank in matched_pairs:
        annotation = annotations[annotation_rank]
        if annotation.atom_count is None:
            continue
        atom_count_compared += 1
        if result.multiplets[predicted_rank].atom_count == annotation.atom_count:
            atom_count_exact += 1
    atom_count_accuracy = atom_count_exact / atom_count_compared if atom_count_compared else None

    matched_predicted_ranks = {pair[0] for pair in matched_pairs}
    matched_annotated_ranks = {pair[1] for pair in matched_pairs}
    return ProtonGroupingMetrics(
        n_predicted=n_predicted,
        n_annotated=n_annotated,
        matched=matched,
        precision=precision,
        recall=recall,
        f1=f1,
        pairwise_accuracy=pairwise_accuracy,
        atom_count_exact=atom_count_exact,
        atom_count_compared=atom_count_compared,
        atom_count_accuracy=atom_count_accuracy,
        unmatched_predicted=tuple(
            result.multiplets[rank].multiplet_id
            for rank in range(n_predicted)
            if rank not in matched_predicted_ranks
        ),
        unmatched_annotated=tuple(
            annotations[rank].label or f"A{rank + 1}"
            for rank in range(n_annotated)
            if rank not in matched_annotated_ranks
        ),
    )


__all__ = [
    "ATOM_COUNT_BASES",
    "METHYL_ATOM_COUNT",
    "METHYL_SOURCES",
    "NUCLEUS",
    "OVERLAP_REGION_KINDS",
    "PROCESSOR_ID",
    "UNCERTAINTY_REASONS",
    "ProtonAnnotation",
    "ProtonConstraintReport",
    "ProtonGrouping",
    "ProtonGroupingMetrics",
    "ProtonMultiplet",
    "ProtonOverlapRegion",
    "ProtonPeakCapacity",
    "ProtonProcessingResult",
    "ProtonProcessorOptions",
    "compare_proton_grouping",
    "process_proton_spectrum",
]
