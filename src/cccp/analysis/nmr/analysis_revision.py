# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Pure peak-edit + evidence-hash half of the NMR analysis revision (T29).

This module is the science half of ``acp.nmr.analysis_revision`` (plan C05-s5
split): manual peak edits and the evidence digest are platform-independent
and live here. The revision *record* and the platform-path/platform-identity
half (``AnalysisRevision``, ``NmrAnalysisSnapshot``, atomic file writes,
``acp.core.models.Structure``) stays in ``acp.nmr.analysis_revision`` and
imports these symbols from here.

A manual experimental-peak revision must never re-run QC. This module owns:

* :class:`PeakEdit` — one manual peak change (shift/label/multiplicity,
  remove, add) applied through :func:`apply_peak_edits`;
* :func:`experiment_peak_digest` — stable digest of the peak list;
* :func:`analysis_evidence_hash` — digest of the ORIGINAL evidence the
  revision is allowed to reuse (candidate identities, cached shieldings,
  effective config, protocol/model versions);
* :func:`verify_evidence_hash` — a changed original evidence set raises
  :class:`EvidenceHashMismatchError` (typed invalidation), never a silent
  recompute on stale shieldings.

``analysis_evidence_hash`` consumes the structural identity fields through
:class:`CandidateIdentity` so no ``acp`` import is needed; ``Structure``
satisfies the protocol structurally.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol, runtime_checkable

from cccp.analysis.identity import identity_fingerprint
from cccp.analysis.nmr.models import (
    ConformerShielding,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    normalize_symbol,
)

__all__ = [
    "ANALYSIS_EVIDENCE_SCHEMA",
    "PEAK_EDIT_OPS",
    "CandidateIdentity",
    "EvidenceHashMismatchError",
    "PeakEdit",
    "PeakEditError",
    "analysis_evidence_hash",
    "apply_peak_edits",
    "experiment_peak_digest",
    "verify_evidence_hash",
]

#: Schema tag for the evidence hash payload (bump on contract change).
ANALYSIS_EVIDENCE_SCHEMA: Final = "acp-nmr-analysis-evidence-v1"

#: Closed vocabulary of manual peak-edit operations.
PEAK_EDIT_OPS: Final[tuple[str, ...]] = ("update", "remove", "add")


@runtime_checkable
class CandidateIdentity(Protocol):
    """Structural identity fields read from an analysis candidate.

    ``acp.core.models.Structure`` satisfies this protocol structurally; the
    pure science layer never imports the platform type.
    """

    id: str
    charge: int
    multiplicity: int
    symbols: Sequence[str]


class PeakEditError(ValueError):
    """A manual peak edit cannot be applied to the base experiment."""


class EvidenceHashMismatchError(ValueError):
    """The analysis basis no longer matches its recorded evidence hash.

    Explicit invalidation: the caller must rebuild the snapshot from the
    current evidence (or re-run QC) instead of revising stale shieldings.
    """

    def __init__(self, expected: str, actual: str) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"analysis evidence hash mismatch: expected {expected!r}, computed {actual!r} — "
            "the cached evidence changed; revision refused (no stale recompute)"
        )


def _normalize_label(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text
    return text[:1].upper() + text[1:]


@dataclass(frozen=True, slots=True)
class PeakEdit:
    """One manual experimental-peak revision (applied in listed order).

    ``op`` is ``"update"`` (change any of shift/label/multiplicity of an
    existing peak), ``"remove"`` (drop an existing peak) or ``"add"``
    (append a new peak; a fresh index is assigned). ``index`` addresses
    the BASE experiment's per-element peak index; indexes stay stable
    across edits so a revision remains traceable.
    """

    op: str
    element: str
    index: int | None = None
    shift_ppm: float | None = None
    atom_label: str | None = None
    multiplicity: int | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if self.op not in PEAK_EDIT_OPS:
            raise ValueError(f"unknown peak-edit op {self.op!r}; expected one of {PEAK_EDIT_OPS}")
        element = normalize_symbol(self.element)
        if not element:
            raise ValueError("PeakEdit.element must be a non-blank element symbol")
        object.__setattr__(self, "element", element)
        if self.op in ("update", "remove"):
            if self.index is None or isinstance(self.index, bool) or self.index < 0:
                raise ValueError(f"PeakEdit[{self.op}] requires a non-negative integer index")
        elif self.index is not None:
            raise ValueError("PeakEdit[add] must not carry an index (assigned on apply)")
        if self.op == "remove":
            if any(
                value is not None for value in (self.shift_ppm, self.atom_label, self.multiplicity)
            ):
                raise ValueError(
                    "PeakEdit[remove] must not carry new shift/label/multiplicity values"
                )
        if self.op == "update" and all(
            value is None for value in (self.shift_ppm, self.atom_label, self.multiplicity)
        ):
            raise ValueError(
                "PeakEdit[update] requires at least one of shift_ppm/atom_label/multiplicity"
            )
        if self.op == "add" and self.shift_ppm is None:
            raise ValueError("PeakEdit[add] requires shift_ppm")
        if self.shift_ppm is not None:
            number = float(self.shift_ppm)
            if not math.isfinite(number):
                raise ValueError(f"PeakEdit.shift_ppm must be finite, got {self.shift_ppm!r}")
            object.__setattr__(self, "shift_ppm", number)
        if self.multiplicity is not None:
            if isinstance(self.multiplicity, bool) or not isinstance(self.multiplicity, int):
                raise ValueError(f"PeakEdit.multiplicity must be an int, got {self.multiplicity!r}")
            if self.multiplicity < 1:
                raise ValueError(f"PeakEdit.multiplicity must be >= 1, got {self.multiplicity!r}")
        if self.atom_label is not None:
            label = _normalize_label(self.atom_label)
            if not label:
                raise ValueError("PeakEdit.atom_label must be a non-blank label or None")
            object.__setattr__(self, "atom_label", label)
        if not isinstance(self.reason, str):
            raise ValueError("PeakEdit.reason must be a string")

    def as_dict(self) -> dict[str, object]:
        """JSON-safe record."""
        return {
            "op": self.op,
            "element": self.element,
            "index": self.index,
            "shift_ppm": self.shift_ppm,
            "atom_label": self.atom_label,
            "multiplicity": self.multiplicity,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PeakEdit:
        """Rebuild from :meth:`as_dict`; a missing field fails explicitly."""
        missing = [name for name in ("op", "element") if name not in payload]
        if missing:
            raise ValueError(f"PeakEdit.from_dict: missing required field(s) {missing}")
        raw_index = payload.get("index")
        raw_shift = payload.get("shift_ppm")
        raw_multiplicity = payload.get("multiplicity")
        raw_label = payload.get("atom_label")
        return cls(
            op=str(payload["op"]),
            element=str(payload["element"]),
            index=int(raw_index) if raw_index is not None else None,
            shift_ppm=float(raw_shift) if raw_shift is not None else None,
            atom_label=str(raw_label) if raw_label is not None else None,
            multiplicity=int(raw_multiplicity) if raw_multiplicity is not None else None,
            reason=str(payload.get("reason", "")),
        )


def _find_peak(peaks: Mapping[str, list[ExperimentalPeak]], edit: PeakEdit) -> ExperimentalPeak:
    group = peaks.get(edit.element)
    if group is None:
        raise PeakEditError(f"no experimental peaks for element {edit.element!r}")
    for peak in group:
        if peak.index == edit.index:
            return peak
    raise PeakEditError(
        f"experimental peak {edit.element}:{edit.index} not found "
        f"(available indexes: {[peak.index for peak in group]})"
    )


def apply_peak_edits(experiment: ExperimentalNmr, edits: Sequence[PeakEdit]) -> ExperimentalNmr:
    """Return a revised copy of *experiment* with *edits* applied in order.

    Peak indexes are stable base identities (never renumbered), so the
    revision record stays traceable to the base spectrum. ``assigned`` is
    recomputed with the parser's all-peaks rule; parse diagnostics from
    the base parse are preserved verbatim. Unknown targets raise
    :class:`PeakEditError` — an edit never silently no-ops.
    """
    peaks: dict[str, list[ExperimentalPeak]] = {
        element: list(group) for element, group in experiment.peaks.items()
    }
    for edit in edits:
        if edit.op == "add":
            group = peaks.setdefault(edit.element, [])
            next_index = (
                max(
                    (peak.index for peak in group if peak.index is not None),
                    default=-1,
                )
                + 1
            )
            label_candidates = (edit.atom_label,) if edit.atom_label is not None else None
            group.append(
                ExperimentalPeak(
                    shift_ppm=float(edit.shift_ppm),
                    element=edit.element,
                    atom_label=edit.atom_label,
                    multiplicity=edit.multiplicity if edit.multiplicity is not None else 1,
                    label_candidates=label_candidates,
                    index=next_index,
                )
            )
            continue
        peak = _find_peak(peaks, edit)
        if edit.op == "remove":
            peaks[edit.element] = [entry for entry in peaks[edit.element] if entry is not peak]
            continue
        label_candidates = peak.label_candidates
        if edit.atom_label is not None:
            label_candidates = (edit.atom_label,)
        updated = replace(
            peak,
            shift_ppm=float(edit.shift_ppm) if edit.shift_ppm is not None else peak.shift_ppm,
            atom_label=edit.atom_label if edit.atom_label is not None else peak.atom_label,
            multiplicity=(
                edit.multiplicity if edit.multiplicity is not None else peak.multiplicity
            ),
            label_candidates=label_candidates,
        )
        peaks[edit.element] = [updated if entry is peak else entry for entry in peaks[edit.element]]

    has_peaks = any(group for group in peaks.values())
    assigned = has_peaks and all(peak.assigned for group in peaks.values() for peak in group)
    return replace(experiment, peaks=peaks, assigned=assigned)


def experiment_peak_digest(experiment: ExperimentalNmr) -> str:
    """Stable digest of the experimental peak list (revision base/target)."""
    payload: dict[str, Any] = {
        "scope": "acp_nmr_experimental_peaks",
        "peaks": [
            {
                "element": element,
                "index": peak.index,
                "shift_ppm": float(peak.shift_ppm),
                "atom_label": peak.atom_label,
                "multiplicity": int(peak.multiplicity),
                "label_candidates": (
                    list(peak.label_candidates) if peak.label_candidates is not None else None
                ),
            }
            for element in sorted(experiment.peaks)
            for peak in experiment.peaks[element]
        ],
        "equivalence_groups": [list(group) for group in experiment.equivalence_groups],
        "omit_atoms": list(experiment.omit_atoms),
    }
    return identity_fingerprint(payload)


def analysis_evidence_hash(
    candidates: Sequence[CandidateIdentity],
    conformer_shieldings: Sequence[Sequence[ConformerShielding]],
    nmr_config: NmrConfig,
    *,
    protocol_id: str | None = None,
    error_model_id: str | None = None,
    dp5_model_id: str | None = None,
) -> str:
    """Digest of the ORIGINAL evidence a revision is allowed to reuse.

    Covers the accumulated per-conformer shieldings (ids, weights, Δ,
    per-atom isotropic values), the candidate identities, the effective
    NMR config and the protocol/model versions. The experimental peaks are
    deliberately excluded — they are the revision axis, tracked by
    :func:`experiment_peak_digest` and
    ``acp.nmr.analysis_revision.PeakRevision``.
    """
    if len(candidates) != len(conformer_shieldings):
        raise ValueError(
            "analysis_evidence_hash: candidates/conformer_shieldings length mismatch "
            f"({len(candidates)} != {len(conformer_shieldings)})"
        )
    payload: dict[str, Any] = {
        "scope": "acp_nmr_analysis_evidence",
        "schema": ANALYSIS_EVIDENCE_SCHEMA,
        "candidates": [
            {
                "id": structure.id,
                "charge": structure.charge,
                "multiplicity": structure.multiplicity,
                "symbols": list(structure.symbols),
            }
            for structure in candidates
        ],
        "conformer_shieldings": [
            [
                {
                    "conformer_id": shielding.conformer_id,
                    "boltzmann_weight": float(shielding.boltzmann_weight),
                    "delta_hartree": (
                        float(shielding.delta_hartree)
                        if shielding.delta_hartree is not None
                        else None
                    ),
                    "symbols": list(shielding.symbols) if shielding.symbols is not None else None,
                    "shieldings": {
                        str(atom_index): {
                            "symbol": str(entry["symbol"]),
                            "isotropic": float(entry["isotropic"]),
                        }
                        for atom_index, entry in sorted(shielding.shieldings.items())
                    },
                }
                for shielding in group
            ]
            for group in conformer_shieldings
        ],
        "nmr_config": nmr_config.to_dict(),
        "protocol_id": protocol_id,
        "error_model_id": error_model_id,
        "dp5_model_id": dp5_model_id,
    }
    return identity_fingerprint(payload)


def verify_evidence_hash(expected: str, actual: str) -> None:
    """Raise :class:`EvidenceHashMismatchError` when the basis changed."""
    if expected != actual:
        raise EvidenceHashMismatchError(expected, actual)
