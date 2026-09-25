"""TS saddle-point validation (plan §12).

Separates *execution* status from *chemical validation* status: an OptTS
run can execute successfully and still fail to be a first-order saddle
point.  All thresholds are recorded with the verdict so the report never
pretends to more precision than the evidence supports.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.typing import NDArray

from acp.calculations.contracts import JsonValue

__all__ = [
    "SIGNIFICANT_IMAGINARY_THRESHOLD_CM1",
    "TsValidation",
    "validate_ts_frequencies",
    "compare_mode_correspondence",
]

#: |ν| below which an imaginary frequency is treated as "near-zero weak
#: imaginary" for interpretation (plan §5.3).  Interpretive only — the raw
#: frequencies are always reported unfiltered.
SIGNIFICANT_IMAGINARY_THRESHOLD_CM1 = 100.0


@dataclass(frozen=True, slots=True)
class TsValidation:
    """Validation verdict for the final frequency analysis."""

    classification: str
    significant_imaginary_cm1: list[float] = field(default_factory=list)
    all_imaginary_cm1: list[float] = field(default_factory=list)
    threshold_cm1: float = SIGNIFICANT_IMAGINARY_THRESHOLD_CM1
    threshold_source: str = "default"
    mode_correspondence: dict[str, JsonValue] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "classification": self.classification,
            "significant_imaginary_cm1": [float(v) for v in self.significant_imaginary_cm1],
            "all_imaginary_cm1": [float(v) for v in self.all_imaginary_cm1],
            "threshold_cm1": float(self.threshold_cm1),
            "threshold_source": self.threshold_source,
            "mode_correspondence": dict(self.mode_correspondence),
            "notes": list(self.notes),
        }


def validate_ts_frequencies(
    frequencies_cm1: list[float],
    *,
    threshold_cm1: float = SIGNIFICANT_IMAGINARY_THRESHOLD_CM1,
    threshold_source: str = "default",
) -> TsValidation:
    """Classify a final frequency list (plan §12 table).

    Classifications:
        ``first_order_saddle_candidate`` — exactly one significant imaginary.
        ``higher_order_saddle``          — more than one significant imaginary.
        ``no_significant_imaginary``     — zero significant imaginary.
        ``not_verified``                 — no frequencies at all.
    """
    if not frequencies_cm1:
        return TsValidation(
            classification="not_verified",
            threshold_cm1=threshold_cm1,
            threshold_source=threshold_source,
            notes=["final frequency analysis unavailable"],
        )
    all_imaginary = sorted(
        (float(value) for value in frequencies_cm1 if float(value) < 0.0),
        reverse=True,
    )
    significant = [value for value in all_imaginary if abs(value) >= threshold_cm1]
    notes: list[str] = []
    weak = [value for value in all_imaginary if abs(value) < threshold_cm1]
    if weak:
        notes.append(
            f"{len(weak)} weak imaginary mode(s) below the {threshold_cm1:g} cm⁻¹ "
            "interpretation threshold (raw values retained)"
        )
    if len(significant) == 1:
        classification = "first_order_saddle_candidate"
    elif len(significant) > 1:
        classification = "higher_order_saddle"
    else:
        classification = "no_significant_imaginary"
        notes.append("no significant imaginary frequency — not verified as a saddle")
    return TsValidation(
        classification=classification,
        significant_imaginary_cm1=significant,
        all_imaginary_cm1=all_imaginary,
        threshold_cm1=threshold_cm1,
        threshold_source=threshold_source,
        notes=notes,
    )


def compare_mode_correspondence(
    source_vector: NDArray[np.float64],
    final_vector: NDArray[np.float64],
    masses_amu: NDArray[np.float64],
    coordinates_angstrom: NDArray[np.float64],
    projector: NDArray[np.float64],
) -> dict[str, JsonValue]:
    """Directional evidence that the final imaginary mode follows the target.

    Large geometry changes can make a raw vector overlap misleading, so the
    verdict is ``manual_review`` whenever the overlap is inconclusive — the
    report never fakes a precise conclusion (plan §12).
    """
    from .source import mass_weight_and_project

    prepared_source = mass_weight_and_project(
        np.asarray(source_vector, dtype=np.float64),
        masses_amu,
        coordinates_angstrom,
        projector,
    )
    prepared_final = mass_weight_and_project(
        np.asarray(final_vector, dtype=np.float64),
        masses_amu,
        coordinates_angstrom,
        projector,
    )
    norm_product = float(np.linalg.norm(prepared_source) * np.linalg.norm(prepared_final))
    if norm_product <= 0.0:
        return {
            "verdict": "manual_review",
            "abs_overlap": None,
            "note": "degenerate zero vector after mass weighting/projection",
        }
    overlap = float(np.abs(np.dot(prepared_source, prepared_final)) / norm_product)
    if overlap >= 0.90:
        verdict = "consistent"
    elif overlap <= 0.50:
        verdict = "inconsistent"
    else:
        verdict = "manual_review"
    return {
        "verdict": verdict,
        "abs_overlap": overlap,
        "note": (
            "absolute overlap of mass-weighted, projected mode vectors; sign "
            "flips represent the same directional subspace"
        ),
    }
