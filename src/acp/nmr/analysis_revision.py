# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryIsInstance=false
"""Analysis-only revision of a completed NMR analysis (todo 47 / gap §8.1/G10).

A manual experimental-peak revision must never re-run QC. This module owns
the revision contract:

* :class:`PeakEdit` — one manual peak change (shift/label/multiplicity,
  remove, add) applied through :func:`apply_peak_edits`;
* :class:`NmrAnalysisSnapshot` — the frozen analysis basis (candidates,
  cached per-conformer shieldings, effective config, protocol/model
  versions) plus its :func:`analysis_evidence_hash`;
* :func:`verify_evidence_hash` — a changed original evidence set raises
  :class:`EvidenceHashMismatchError` (typed invalidation), never a silent
  recompute on stale shieldings;
* :class:`AnalysisRevision` — the persisted record linking the preserved
  base report to the new revision report (``report_before`` →
  ``report_after``), with the peak revision digests and the resulting
  analysis identity;
* :func:`review_only_status` — a revision that needs human review maps to
  the EXISTING scheduler review state (``JobStatus.WAITING_REVIEW``); no
  new lifecycle entry point is introduced.

The recomputation itself lives in ``acp.workflows.nmr.revise_nmr_analysis``
(pure analysis stages only: averaging/assignment/DP4/DP5/report over the
snapshot's cached shieldings).
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from acp.calculations.identity import identity_fingerprint
from acp.core.models import Structure
from acp.nmr.error_model import load_error_model
from acp.nmr.models import (
    ConformerShielding,
    EnsembleQuality,
    ExperimentalNmr,
    ExperimentalPeak,
    NmrConfig,
    NmrReport,
    normalize_symbol,
)

__all__ = [
    "ANALYSIS_EVIDENCE_SCHEMA",
    "ANALYSIS_REVISION_SCHEMA",
    "PEAK_EDIT_OPS",
    "REVIEW_STATUS_WAITING_REVIEW",
    "AnalysisRevision",
    "EvidenceHashMismatchError",
    "NmrAnalysisSnapshot",
    "PeakEdit",
    "PeakEditError",
    "PeakRevision",
    "analysis_evidence_hash",
    "analysis_result_identity",
    "apply_peak_edits",
    "experiment_peak_digest",
    "load_revision_index",
    "load_revision_record",
    "review_only_status",
    "revision_identity",
    "utc_now_iso",
    "verify_evidence_hash",
    "write_revision_record",
]

logger = logging.getLogger(__name__)

#: Schema tags for the two hash payloads (bump on contract change).
ANALYSIS_EVIDENCE_SCHEMA: Final = "acp-nmr-analysis-evidence-v1"
ANALYSIS_REVISION_SCHEMA: Final = "acp-nmr-analysis-revision-v1"

#: Closed vocabulary of manual peak-edit operations.
PEAK_EDIT_OPS: Final[tuple[str, ...]] = ("update", "remove", "add")

#: The EXISTING scheduler review state (``acp.scheduler.jobs.JobStatus``)
#: reused verbatim — a revision never introduces a new lifecycle entry.
REVIEW_STATUS_WAITING_REVIEW: Final = "waiting_review"

REVISION_FILENAME: Final = "revision.json"
REVISION_INDEX_FILENAME: Final = "revisions.json"


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
    candidates: Sequence[Structure],
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
    :func:`experiment_peak_digest` and :class:`PeakRevision`.
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


@dataclass(frozen=True, slots=True)
class PeakRevision:
    """The experimental-peak revision axis of one :class:`AnalysisRevision`."""

    base_digest: str
    revised_digest: str
    edits: tuple[PeakEdit, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "edits", tuple(self.edits))
        if not all(isinstance(edit, PeakEdit) for edit in self.edits):
            raise ValueError("PeakRevision.edits must contain PeakEdit records")
        if not self.base_digest or not self.revised_digest:
            raise ValueError("PeakRevision digests must be non-blank")
        if not isinstance(self.reason, str):
            raise ValueError("PeakRevision.reason must be a string")

    def as_dict(self) -> dict[str, object]:
        """JSON-safe record."""
        return {
            "base_digest": self.base_digest,
            "revised_digest": self.revised_digest,
            "edits": [edit.as_dict() for edit in self.edits],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> PeakRevision:
        """Rebuild from :meth:`as_dict`."""
        missing = [name for name in ("base_digest", "revised_digest") if name not in payload]
        if missing:
            raise ValueError(f"PeakRevision.from_dict: missing required field(s) {missing}")
        raw_edits = payload.get("edits", [])
        if not isinstance(raw_edits, list):
            raise ValueError("PeakRevision.from_dict: edits must be a list")
        return cls(
            base_digest=str(payload["base_digest"]),
            revised_digest=str(payload["revised_digest"]),
            edits=tuple(PeakEdit.from_dict(entry) for entry in raw_edits),
            reason=str(payload.get("reason", "")),
        )


def revision_identity(
    *,
    base_evidence_hash: str,
    peak_revision: PeakRevision,
    protocol_id: str | None,
    error_model_id: str,
    dp5_model_id: str | None,
) -> str:
    """Deterministic id of one revision (same inputs → same id, idempotent)."""
    payload: dict[str, Any] = {
        "scope": "acp_nmr_analysis_revision",
        "schema": ANALYSIS_REVISION_SCHEMA,
        "base_evidence_hash": base_evidence_hash,
        "base_peak_digest": peak_revision.base_digest,
        "revised_peak_digest": peak_revision.revised_digest,
        "edits": [edit.as_dict() for edit in peak_revision.edits],
        "protocol_id": protocol_id,
        "error_model_id": error_model_id,
        "dp5_model_id": dp5_model_id,
    }
    return identity_fingerprint(payload)


def analysis_result_identity(report: NmrReport) -> str:
    """Digest of the resulting analysis (revision report payload identity)."""
    payload: dict[str, Any] = {
        "scope": "acp_nmr_analysis_result",
        "schema": ANALYSIS_REVISION_SCHEMA,
        "report": report.as_dict(),
    }
    return identity_fingerprint(payload)


def review_only_status(requires_review: bool) -> str | None:
    """Map a review requirement onto the EXISTING scheduler review state.

    Returns ``JobStatus.WAITING_REVIEW``'s value when review is needed and
    ``None`` otherwise. The revision adds no lifecycle entry: human review
    continues through the scheduler's review-only ``resume`` path.
    """
    return REVIEW_STATUS_WAITING_REVIEW if requires_review else None


@dataclass(frozen=True, slots=True)
class AnalysisRevision:
    """Persisted record linking a preserved base report to its revision."""

    revision_id: str
    base_evidence_hash: str
    peak_revision: PeakRevision
    protocol_id: str | None
    error_model_id: str
    dp5_model_id: str | None
    report_before: str
    report_after: str
    result_identity: str
    created_at: str
    requires_review: bool = False
    review_status: str = ""

    def __post_init__(self) -> None:
        if not self.revision_id.strip():
            raise ValueError("AnalysisRevision.revision_id must be non-blank")
        if not self.base_evidence_hash.strip():
            raise ValueError("AnalysisRevision.base_evidence_hash must be non-blank")
        if self.requires_review:
            expected = review_only_status(True)
            if self.review_status != expected:
                raise ValueError(
                    "AnalysisRevision.review_status must be "
                    f"{expected!r} when review is required, got {self.review_status!r}"
                )
        elif self.review_status:
            raise ValueError(
                "AnalysisRevision.review_status must be empty when no review is required"
            )

    def as_dict(self) -> dict[str, object]:
        """JSON-safe record."""
        return {
            "schema": ANALYSIS_REVISION_SCHEMA,
            "revision_id": self.revision_id,
            "base_evidence_hash": self.base_evidence_hash,
            "peak_revision": self.peak_revision.as_dict(),
            "protocol_id": self.protocol_id,
            "error_model_id": self.error_model_id,
            "dp5_model_id": self.dp5_model_id,
            "report_before": self.report_before,
            "report_after": self.report_after,
            "result_identity": self.result_identity,
            "created_at": self.created_at,
            "requires_review": self.requires_review,
            "review_status": self.review_status,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> AnalysisRevision:
        """Rebuild from :meth:`as_dict`; a missing field fails explicitly."""
        required = (
            "revision_id",
            "base_evidence_hash",
            "peak_revision",
            "error_model_id",
            "report_before",
            "report_after",
            "result_identity",
            "created_at",
        )
        missing = [name for name in required if name not in payload]
        if missing:
            raise ValueError(f"AnalysisRevision.from_dict: missing required field(s) {missing}")
        raw_peak_revision = payload["peak_revision"]
        if not isinstance(raw_peak_revision, Mapping):
            raise ValueError("AnalysisRevision.from_dict: peak_revision must be a mapping")
        raw_protocol = payload.get("protocol_id")
        raw_dp5 = payload.get("dp5_model_id")
        return cls(
            revision_id=str(payload["revision_id"]),
            base_evidence_hash=str(payload["base_evidence_hash"]),
            peak_revision=PeakRevision.from_dict(raw_peak_revision),
            protocol_id=str(raw_protocol) if raw_protocol is not None else None,
            error_model_id=str(payload["error_model_id"]),
            dp5_model_id=str(raw_dp5) if raw_dp5 is not None else None,
            report_before=str(payload["report_before"]),
            report_after=str(payload["report_after"]),
            result_identity=str(payload["result_identity"]),
            created_at=str(payload["created_at"]),
            requires_review=bool(payload.get("requires_review", False)),
            review_status=str(payload.get("review_status", "")),
        )


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def write_revision_record(
    directory: Path | str,
    revision: AnalysisRevision,
    *,
    index_root: Path | str | None = None,
) -> Path:
    """Atomically write ``revision.json`` (plus the index when requested).

    Returns the revision record path. The base report is never touched:
    the record only links ``report_before`` → ``report_after``.
    """
    directory = Path(directory)
    record_path = directory / REVISION_FILENAME
    _atomic_write_json(record_path, revision.as_dict())
    if index_root is not None:
        index_path = Path(index_root) / REVISION_INDEX_FILENAME
        index: dict[str, object] = {}
        if index_path.is_file():
            try:
                raw = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                logger.warning("revision index %s unreadable (%s); rebuilding", index_path, exc)
                raw = {}
            if isinstance(raw, dict):
                index = {str(key): value for key, value in raw.items()}
        index[revision.revision_id] = revision.as_dict()
        _atomic_write_json(index_path, index)
    return record_path


def load_revision_record(path: Path | str) -> AnalysisRevision:
    """Load one ``revision.json`` record; malformed input raises ``ValueError``."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"revision record {path!s} is not a JSON object")
    return AnalysisRevision.from_dict(payload)


def load_revision_index(index_root: Path | str) -> dict[str, AnalysisRevision]:
    """Load the revisions index (``revisions.json``); malformed entries are skipped."""
    index_path = Path(index_root) / REVISION_INDEX_FILENAME
    if not index_path.is_file():
        return {}
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning("revision index %s unreadable (%s)", index_path, exc)
        return {}
    if not isinstance(payload, Mapping):
        logger.warning("revision index %s is not a JSON object", index_path)
        return {}
    records: dict[str, AnalysisRevision] = {}
    for revision_id, raw in payload.items():
        try:
            if not isinstance(raw, Mapping):
                raise ValueError("record is not an object")
            records[str(revision_id)] = AnalysisRevision.from_dict(raw)
        except ValueError as exc:
            logger.warning("revision index entry %s malformed (%s); skipped", revision_id, exc)
    return records


@dataclass(frozen=True, slots=True)
class NmrAnalysisSnapshot:
    """Frozen analysis basis of a completed NMR run (todo 47).

    Holds the cached per-conformer shieldings (already finalized by the
    workflow quality gate — reported weights = algorithm input), the
    candidates, the effective config and the protocol/model versions.
    :meth:`create` computes the :func:`analysis_evidence_hash`; a snapshot
    whose evidence changed must fail verification before any recompute.
    """

    candidates: tuple[Structure, ...]
    conformer_shieldings: tuple[tuple[ConformerShielding, ...], ...]
    experiment: ExperimentalNmr
    nmr_config: NmrConfig
    output_root: Path
    base_report_path: Path
    evidence_hash: str
    ensemble_qualities: tuple[EnsembleQuality | None, ...] = ()
    generated_ensembles: tuple[bool, ...] = ()
    protocol_id: str | None = None
    error_model_id: str = ""
    dp5_model_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(
            self,
            "conformer_shieldings",
            tuple(tuple(group) for group in self.conformer_shieldings),
        )
        n_candidates = len(self.candidates)
        if len(self.conformer_shieldings) != n_candidates:
            raise ValueError(
                "NmrAnalysisSnapshot: conformer_shieldings length must match candidates "
                f"({len(self.conformer_shieldings)} != {n_candidates})"
            )
        object.__setattr__(self, "output_root", Path(self.output_root))
        object.__setattr__(self, "base_report_path", Path(self.base_report_path))
        if not self.ensemble_qualities:
            object.__setattr__(self, "ensemble_qualities", (None,) * n_candidates)
        else:
            object.__setattr__(self, "ensemble_qualities", tuple(self.ensemble_qualities))
            if len(self.ensemble_qualities) != n_candidates:
                raise ValueError(
                    "NmrAnalysisSnapshot: ensemble_qualities length must match candidates"
                )
        if not self.generated_ensembles:
            object.__setattr__(self, "generated_ensembles", (False,) * n_candidates)
        else:
            object.__setattr__(self, "generated_ensembles", tuple(self.generated_ensembles))
            if len(self.generated_ensembles) != n_candidates:
                raise ValueError(
                    "NmrAnalysisSnapshot: generated_ensembles length must match candidates"
                )
        if not self.evidence_hash.strip():
            raise ValueError("NmrAnalysisSnapshot.evidence_hash must be non-blank")

    @classmethod
    def create(
        cls,
        *,
        candidates: Sequence[Structure],
        conformer_shieldings: Sequence[Sequence[ConformerShielding]],
        experiment: ExperimentalNmr,
        nmr_config: NmrConfig,
        output_root: str | Path,
        base_report_path: str | Path,
        ensemble_qualities: Sequence[EnsembleQuality | None] | None = None,
        generated_ensembles: Sequence[bool] | None = None,
        protocol_id: str | None = None,
        error_model_id: str | None = None,
        dp5_model_id: str | None = None,
    ) -> NmrAnalysisSnapshot:
        """Freeze an analysis basis and bind it to its evidence hash.

        ``error_model_id`` defaults to the resolved id of
        ``nmr_config.error_model`` (the model version the analysis ran
        with), never to the raw requested name.
        """
        resolved_error_model = (
            error_model_id
            if error_model_id is not None
            else load_error_model(nmr_config.error_model).model_id
        )
        evidence_hash = analysis_evidence_hash(
            candidates,
            conformer_shieldings,
            nmr_config,
            protocol_id=protocol_id,
            error_model_id=resolved_error_model,
            dp5_model_id=dp5_model_id,
        )
        return cls(
            candidates=tuple(candidates),
            conformer_shieldings=tuple(tuple(group) for group in conformer_shieldings),
            experiment=experiment,
            nmr_config=nmr_config,
            output_root=Path(output_root),
            base_report_path=Path(base_report_path),
            evidence_hash=evidence_hash,
            ensemble_qualities=tuple(ensemble_qualities) if ensemble_qualities is not None else (),
            generated_ensembles=(
                tuple(generated_ensembles) if generated_ensembles is not None else ()
            ),
            protocol_id=protocol_id,
            error_model_id=resolved_error_model,
            dp5_model_id=dp5_model_id,
        )


def utc_now_iso() -> str:
    """UTC ISO-8601 timestamp for revision records (never used in paths)."""
    return datetime.now(timezone.utc).isoformat()
