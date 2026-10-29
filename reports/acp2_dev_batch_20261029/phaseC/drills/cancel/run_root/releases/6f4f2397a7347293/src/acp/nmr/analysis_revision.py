# pyright: reportMissingTypeStubs=false, reportExplicitAny=false, reportAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false, reportUnusedParameter=false, reportUnusedCallResult=false, reportUnnecessaryIsInstance=false
"""Analysis-only revision record of a completed NMR analysis (todo 47 / gap §8.1/G10).

Platform half of the T29 split: the pure peak-edit/evidence-hash science
lives in ``cccp.analysis.nmr.analysis_revision`` and is re-exported here so
existing ``acp.nmr.analysis_revision`` imports keep working on the same
objects. This module keeps the revision *record*, the atomic platform paths
and the ``acp.core.models.Structure`` coupling:

* :class:`PeakRevision` — the experimental-peak revision axis;
* :class:`AnalysisRevision` — the persisted record linking the preserved
  base report to the new revision report (``report_before`` →
  ``report_after``), with the peak revision digests and the resulting
  analysis identity;
* :class:`NmrAnalysisSnapshot` — the frozen analysis basis (candidates,
  cached per-conformer shieldings, effective config, protocol/model
  versions) plus its evidence hash; candidates are platform
  ``Structure`` objects, hence the platform side;
* :func:`write_revision_record` / :func:`load_revision_record` /
  :func:`load_revision_index` — atomic record/index persistence;
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
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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
    NmrConfig,
    NmrReport,
)
from cccp.analysis.nmr.analysis_revision import (
    ANALYSIS_EVIDENCE_SCHEMA,
    PEAK_EDIT_OPS,
    EvidenceHashMismatchError,
    PeakEdit,
    PeakEditError,
    analysis_evidence_hash,
    apply_peak_edits,
    experiment_peak_digest,
    verify_evidence_hash,
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

#: Schema tag for the revision record payload (bump on contract change).
ANALYSIS_REVISION_SCHEMA: Final = "acp-nmr-analysis-revision-v1"

#: The EXISTING scheduler review state (``acp.scheduler.jobs.JobStatus``)
#: reused verbatim — a revision never introduces a new lifecycle entry.
REVIEW_STATUS_WAITING_REVIEW: Final = "waiting_review"

REVISION_FILENAME: Final = "revision.json"
REVISION_INDEX_FILENAME: Final = "revisions.json"


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
    :meth:`create` computes the
    :func:`cccp.analysis.nmr.analysis_revision.analysis_evidence_hash`; a
    snapshot whose evidence changed must fail verification before any
    recompute.
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
