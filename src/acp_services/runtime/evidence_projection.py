"""M1-03 — the single platform evidence projection (G07/G08).

This module is the **only** place where the platform turns a completed attempt
into something a research client may consume.  It does four things and refuses
to do a fifth:

1. Normalise the platform task reference.  The frozen application shape is
   ``{project_id, task_id, attempt, release:{release_id, release_digest}}`` with
   ``job_id`` as the legacy alias; a missing ``attempt`` or ``release`` is a
   refusal, never a default of ``1``/``""`` (see :func:`parse_task_ref`).
2. Admit an evidence document through the frozen B18 registration vocabulary
   (:func:`acp_services.runtime.science_binding.validate_evidence_report`) and
   then through A's public schema parser
   (:meth:`cccp.analysis.research.evidence.EvidenceReport.from_dict`).  No
   second scientific validator lives here: the claim vocabulary, the claim
   evidence floor and the validator namespace are A's.
3. Verify the digest binding of everything it consumes: the ``task_result``
   content digest (the worker's own canonicalisation), the receipt's
   ``evidence_digests["task_result_digest"]`` pointer, the report bytes and —
   through A's :func:`cccp.analysis.research.evidence.verify_claim_sources` —
   every digested claim source below the controlled read root.
4. Project the result into one :class:`EvidenceView` and, independently, one
   :class:`ResearchDecision`.  The decision vocabulary is the frozen five
   values of ``acp2.runtime.research-session.v1`` (``accept``/``revise``/
   ``reject``/``defer``/``escalate``) and is disjoint from every task/job
   status.

The fifth thing — deciding what a scientific claim *means* — belongs to A and
is not implemented here.  ``supported``/``refuted``/``inconclusive``/
``not_evaluated`` are opaque strings to this module; a completed execution is
only ``task_execution_completed`` and never ``claim_supported``.  Model text is
never evidence: :func:`cccp.analysis.research.evidence.reject_model_conclusion_as_evidence`
runs on every admitted document, and the decision record carries rationale
codes only — there is no free-text field for a model conclusion to occupy.

Isolation: ``src/acp_services/**`` never imports the legacy ``acp`` package.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from acp_services.contracts.envelope import RECEIPT_STATES
from acp_services.data.transfer import sha256_bytes
from acp_services.runtime import worker
from acp_services.runtime.research_session import (
    DECISION_VALUES,
    EvidenceRef,
    ResearchIdentity,
)
from acp_services.runtime.science_binding import validate_evidence_report
from cccp.analysis.research.evidence import (
    Claim,
    EvidenceContractError,
    EvidenceDigestMismatchError,
    EvidencePathError,
    EvidenceReport,
    EvidenceSourceUnavailableError,
    ModelConclusionError,
    SourceVerification,
    reject_model_conclusion_as_evidence,
    verify_claim_sources,
)

__all__ = [
    "ACCEPT_PRECONDITION_CODES",
    "DECISIONS",
    "DECISION_RATIONALE_CODES",
    "EVIDENCE_PROJECTION_SCHEMA",
    "EVIDENCE_PROJECTION_VERSION",
    "EVIDENCE_STATUSES",
    "EXECUTION_TERMINAL_STATES",
    "PUBLISH_STATUS_TO_EVIDENCE_STATUS",
    "QUALIFICATION_TO_EVIDENCE_STATUS",
    "RECEIPT_STATE_TO_EVIDENCE_STATUS",
    "RESEARCH_DECISION_SCHEMA",
    "STEP_STATUS_TO_EVIDENCE_STATUS",
    "TASK_REF_SCHEMA",
    "ClaimProjection",
    "EvidenceProjectionError",
    "EvidenceView",
    "ResearchDecision",
    "ResearchDecisionError",
    "TaskRef",
    "TaskRefError",
    "combine_evidence_status",
    "decide_evidence",
    "decision_from_dict",
    "evidence_status_for_qualification",
    "evidence_status_for_step",
    "parse_evidence_report",
    "parse_task_ref",
    "project_evidence",
    "validate_research_decision",
    "verify_attribution",
]

#: Wire identity of the projected evidence view.
EVIDENCE_PROJECTION_SCHEMA = "acp2.runtime.evidence-projection.v1"
EVIDENCE_PROJECTION_VERSION = "1.0.0"
#: Wire identity of the normalised task reference.
TASK_REF_SCHEMA = "acp2.runtime.task-ref.v1"
#: Wire identity of the independent research decision.
RESEARCH_DECISION_SCHEMA = "acp2.runtime.research-decision.v1"

#: The frozen evidence-completion vocabulary (M1-03).  It is *execution*
#: completeness — worker qualification, step outcome and publication combined —
#: and is deliberately disjoint from A's four claim statuses.
EVIDENCE_STATUSES: tuple[str, ...] = ("complete", "partial", "incomplete", "failed")

#: Worker qualification (``task_result.json``) -> evidence status.
QUALIFICATION_TO_EVIDENCE_STATUS: Mapping[str, str] = MappingProxyType(
    {
        worker.QUALIFICATION_COMPLETE: "complete",
        worker.QUALIFICATION_PARTIAL: "partial",
        worker.QUALIFICATION_INCOMPLETE: "incomplete",
        worker.QUALIFICATION_TASK_FAILED: "failed",
    }
)

#: Step status (``acp_services.runtime.step_dag.StepStatus``) -> evidence status.
STEP_STATUS_TO_EVIDENCE_STATUS: Mapping[str, str] = MappingProxyType(
    {
        "completed": "complete",
        "failed": "failed",
        "blocked": "failed",
        "upstream_failed": "failed",
        "cancelled": "failed",
        "pending": "incomplete",
        "ready": "incomplete",
        "running": "incomplete",
    }
)

#: Step publication status (``acp_services.runtime.step_dag.PublishStatus``).
PUBLISH_STATUS_TO_EVIDENCE_STATUS: Mapping[str, str] = MappingProxyType(
    {
        "published": "complete",
        "not_published": "incomplete",
        "failed": "failed",
    }
)

#: Receipt terminal state -> evidence status (``blocked`` is a failure, as in
#: ``worker.JOB_STATUS_BY_RECEIPT_STATE``).
RECEIPT_STATE_TO_EVIDENCE_STATUS: Mapping[str, str] = MappingProxyType(
    {
        "completed": "complete",
        "failed": "failed",
        "cancelled": "failed",
        "blocked": "failed",
    }
)

#: A report's execution terminal states (A's vocabulary, re-used verbatim).
EXECUTION_TERMINAL_STATES: tuple[str, ...] = ("completed", "failed", "cancelled", "not_started")

#: The frozen research decision vocabulary — the single source is the M1-02
#: contract module, never a local copy.
DECISIONS: tuple[str, ...] = tuple(DECISION_VALUES)

#: Rationale codes are derived from the view; a decision record has no free
#: text field, so a model narrative can never be published as a reason.
DECISION_RATIONALE_CODES: tuple[str, ...] = (
    "execution_completed",
    "execution_not_completed",
    "evidence_complete",
    "evidence_partial",
    "evidence_incomplete",
    "evidence_failed",
    "claim_supported",
    "claim_refuted",
    "claim_inconclusive",
    "claim_not_evaluated",
    "blocking_missing_check",
    "digest_bound_evidence",
    "no_digest_bound_evidence",
)

#: ``accept`` is the only decision with preconditions, in this evaluation order.
#: A blocking missing check is reported before the generic claim-status rule so
#: the actionable cause is explicit; both are refusals either way.
ACCEPT_PRECONDITION_CODES: tuple[str, ...] = (
    "execution_not_completed",
    "evidence_not_complete",
    "blocking_missing_check",
    "claim_status_not_supported",
    "evidence_ref_required",
)

_SEGMENT_BANNED = ("|", "\n", "\r")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")

#: A's frozen claim vocabulary (the statuses the projection never upgrades).
_CLAIM_STATUSES: tuple[str, ...] = ("supported", "refuted", "inconclusive", "not_evaluated")

#: Closed envelope of the independent decision record (no free-text field).
_DECISION_FIELDS = frozenset(
    {
        "schema",
        "project_id",
        "research_id",
        "round",
        "decision",
        "task_ref",
        "evidence",
        "evidence_refs",
        "rationale_codes",
        "projection_digest",
    }
)
_DECISION_EVIDENCE_FIELDS = frozenset({"status", "overall_claim_status", "blocking_missing_checks"})


# ---------------------------------------------------------------------------
# Typed refusals
# ---------------------------------------------------------------------------


class EvidenceProjectionError(RuntimeError):
    """A projection refusal with a stable code; never a silent coercion."""

    code = "evidence_projection_error"

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


class TaskRefError(EvidenceProjectionError):
    """The task reference is not uniquely identifiable."""

    code = "task_ref_invalid"


class ResearchDecisionError(EvidenceProjectionError):
    """The decision is not publishable for this evidence view."""

    code = "research_decision_invalid"


def _segment(value: Any, label: str, *, code: str) -> str:
    text = str(value or "")
    if not text or not text.strip():
        raise TaskRefError(code, f"{label} is required")
    if text != text.strip():
        raise TaskRefError(code, f"{label} must not carry surrounding whitespace")
    for banned in _SEGMENT_BANNED:
        if banned in text:
            raise TaskRefError(code, f"{label} must not contain {banned!r}")
    return text


def _digest(value: Any, label: str, *, code: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.match(text) is None:
        raise TaskRefError(code, f"{label} must be sha256:<64 lowercase hex>")
    return text


# ---------------------------------------------------------------------------
# TaskRef: explicit normalisation, no defaulted attempt/release
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TaskRef:
    """One verifiable task attempt: project + task + attempt + release.

    ``job_id`` is the legacy alias the frozen application contract keeps while
    the legacy job row exists; it is normalised to ``task_id`` and can never
    name a different attempt.
    """

    project_id: str
    task_id: str
    job_id: str
    attempt: int
    release_id: str
    release_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "project_id",
            _segment(self.project_id, "project_id", code="task_ref_missing_project"),
        )
        task_id = _segment(self.task_id, "task_id", code="task_ref_missing_task_id")
        if _TASK_ID_RE.match(task_id) is None:
            raise TaskRefError("task_ref_invalid_task_id", f"task_id {task_id!r} is not a task id")
        object.__setattr__(self, "task_id", task_id)
        job_id = str(self.job_id or task_id)
        if job_id != task_id:
            raise TaskRefError(
                "task_ref_alias_conflict",
                f"job_id {job_id!r} is the legacy alias of task_id {task_id!r}",
            )
        object.__setattr__(self, "job_id", task_id)
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise TaskRefError("task_ref_missing_attempt", "attempt must be an explicit integer")
        if self.attempt < 1:
            raise TaskRefError("task_ref_invalid_attempt", "attempt must be >= 1")
        object.__setattr__(
            self,
            "release_id",
            _segment(self.release_id, "release_id", code="task_ref_missing_release"),
        )
        object.__setattr__(
            self,
            "release_digest",
            _digest(self.release_digest, "release_digest", code="task_ref_invalid_release_digest"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, project_id: str = "") -> TaskRef:
        """Normalise a platform/RPH task reference.

        ``attempt`` and ``release`` are **required**: an omitted attempt is not
        attempt 1 and an omitted release is not an empty release.  ``release``
        may be the frozen nested object (``release_id``/``release_digest`` or
        ``id``/``digest``) or the flat ``release_id``/``release_digest`` pair
        used by the research-session task binding.
        """
        if not isinstance(payload, Mapping):
            raise TaskRefError("task_ref_invalid", "a task reference must be an object")
        project = str(payload.get("project_id") or project_id or "")
        if not project:
            raise TaskRefError("task_ref_missing_project", "project_id is required")
        raw_task_id = payload.get("task_id")
        raw_job_id = payload.get("job_id")
        task_id = str(raw_task_id or raw_job_id or "")
        if not task_id:
            raise TaskRefError(
                "task_ref_missing_task_id", "task_id (or its job_id alias) is required"
            )
        if raw_task_id and raw_job_id and str(raw_task_id) != str(raw_job_id):
            raise TaskRefError(
                "task_ref_alias_conflict",
                f"task_id {raw_task_id!r} != job_id {raw_job_id!r}",
            )
        if "attempt" not in payload:
            raise TaskRefError(
                "task_ref_missing_attempt", "attempt is required; it is never defaulted"
            )
        attempt = payload.get("attempt")
        if isinstance(attempt, bool) or not isinstance(attempt, int):
            raise TaskRefError("task_ref_missing_attempt", "attempt must be an explicit integer")
        release = payload.get("release")
        if release is None:
            release_id = payload.get("release_id")
            release_digest = payload.get("release_digest")
        elif isinstance(release, Mapping):
            release_id = release.get("release_id") or release.get("id")
            release_digest = release.get("release_digest") or release.get("digest")
        else:
            raise TaskRefError("task_ref_missing_release", "release must be an object")
        if not release_id:
            raise TaskRefError("task_ref_missing_release", "release.release_id is required")
        if not release_digest:
            raise TaskRefError(
                "task_ref_missing_release_digest", "release.release_digest is required"
            )
        return cls(
            project_id=project,
            task_id=task_id,
            job_id=str(raw_job_id or task_id),
            attempt=attempt,
            release_id=str(release_id),
            release_digest=str(release_digest),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen application task-ref shape (nested release)."""
        return {
            "schema": TASK_REF_SCHEMA,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "release": {
                "release_id": self.release_id,
                "release_digest": self.release_digest,
            },
        }

    @classmethod
    def from_task_binding(cls, binding: Any, *, release_digest: str) -> TaskRef:
        """Normalise an M1-02 ``TaskBinding`` into a ``TaskRef``.

        The binding is flat and carries no release digest, so it is **not** a
        ``TaskRef`` on its own: the verified digest is supplied explicitly and
        an empty one is refused (a binding alone is not enough to consume
        evidence).
        """
        try:
            payload = binding.to_dict() if hasattr(binding, "to_dict") else dict(binding)
        except (TypeError, ValueError) as exc:
            raise TaskRefError("task_ref_invalid", f"not a task binding: {exc}") from exc
        return cls.from_dict({**payload, "release_digest": release_digest})

    def identity(self) -> tuple[str, str, int, str, str]:
        """Hashable identity of the attempt (no display fields)."""
        return (
            self.project_id,
            self.task_id,
            self.attempt,
            self.release_id,
            self.release_digest,
        )


def parse_task_ref(payload: Mapping[str, Any], *, project_id: str = "") -> TaskRef:
    """Parse a task reference through the single normalisation above."""
    return TaskRef.from_dict(payload, project_id=project_id)


# ---------------------------------------------------------------------------
# Status vocabulary mapping (frozen)
# ---------------------------------------------------------------------------


def _status_rank(status: str) -> int:
    return EVIDENCE_STATUSES.index(status)


def combine_evidence_status(*statuses: str) -> str:
    """Worst-of combination; an unknown status is refused, never upgraded."""
    worst = "complete"
    for status in statuses:
        if not status:
            continue
        if status not in EVIDENCE_STATUSES:
            raise EvidenceProjectionError(
                "unknown_evidence_status",
                f"{status!r} is not one of {list(EVIDENCE_STATUSES)}",
            )
        if _status_rank(status) > _status_rank(worst):
            worst = status
    return worst


def evidence_status_for_qualification(qualification: str) -> str:
    """Map the worker's authoritative qualification onto the frozen four."""
    status = QUALIFICATION_TO_EVIDENCE_STATUS.get(str(qualification or ""))
    if status is None:
        raise EvidenceProjectionError(
            "unknown_qualification",
            f"qualification {qualification!r} is not one of "
            f"{sorted(QUALIFICATION_TO_EVIDENCE_STATUS)}",
        )
    return status


def evidence_status_for_step(status: str, publish_status: str = "") -> str:
    """Map one step outcome (execution + publication) onto the frozen four."""
    mapped = STEP_STATUS_TO_EVIDENCE_STATUS.get(str(status or ""))
    if mapped is None:
        raise EvidenceProjectionError(
            "unknown_step_status", f"step status {status!r} is not a StepStatus"
        )
    if not publish_status:
        return mapped
    published = PUBLISH_STATUS_TO_EVIDENCE_STATUS.get(str(publish_status))
    if published is None:
        raise EvidenceProjectionError(
            "unknown_publish_status", f"publish status {publish_status!r} is not a PublishStatus"
        )
    return combine_evidence_status(mapped, published)


# ---------------------------------------------------------------------------
# Report admission: A's vocabulary, then A's parser, then the model guard
# ---------------------------------------------------------------------------


def parse_evidence_report(document: Any) -> EvidenceReport:
    """Admit an evidence document through the single B -> A parse path.

    The document first passes the frozen B18 registration vocabulary
    (``science_binding.validate_evidence_report``) and then A's closed schema
    parser.  A model/LLM conclusion is refused by both layers.
    """
    validate_evidence_report(document)
    if isinstance(document, EvidenceReport):
        report = document
    else:
        try:
            reject_model_conclusion_as_evidence(document)
            report = EvidenceReport.from_dict(document)
        except (
            EvidenceContractError,
            ModelConclusionError,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise EvidenceProjectionError("invalid_science_report", str(exc)) from exc
    return report


def _report_mapping(report: bytes | bytearray | str) -> dict[str, Any]:
    if isinstance(report, (bytes, bytearray)):
        raw = bytes(report)
    elif isinstance(report, str):
        raw = report.encode("utf-8")
    else:
        raise EvidenceProjectionError(
            "report_not_digest_bound",
            "an evidence report is admitted as its exact bytes; a parsed mapping is not evidence",
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceProjectionError(
            "invalid_science_report", f"unreadable report: {exc}"
        ) from exc
    if not isinstance(document, dict):
        raise EvidenceProjectionError("invalid_science_report", "the report is not a JSON object")
    return document


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimProjection:
    """One projected claim: status preserved, never invented or upgraded."""

    claim_id: str
    status: str
    validator_id: str = ""
    validator_version: str = ""
    source_ref_count: int = 0
    blocking_missing_checks: tuple[str, ...] = ()

    @classmethod
    def from_claim(cls, claim: Claim) -> ClaimProjection:
        validator = claim.validator
        return cls(
            claim_id=claim.claim_id,
            status=claim.status,
            validator_id="" if validator is None else validator.validator_id,
            validator_version="" if validator is None else validator.validator_version,
            source_ref_count=len(claim.source_refs),
            blocking_missing_checks=tuple(
                check.check_id for check in claim.missing_checks if check.blocking
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "source_ref_count": self.source_ref_count,
            "blocking_missing_checks": list(self.blocking_missing_checks),
        }


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """The single projection a research client consumes.

    It carries references and statuses only: the report body stays in the
    platform artifact store, addressed by ``report.digest``.
    """

    identity: ResearchIdentity
    round: int
    task_ref: TaskRef
    report_id: str
    report_digest: str
    report_schema_version: int
    subject_id: str
    execution_terminal_state: str
    qualification: str
    evidence_status: str
    claims: tuple[ClaimProjection, ...]
    overall_claim_status: str
    blocking_missing_checks: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    limitations: tuple[str, ...]
    provenance_digest: str
    normal_reuse: bool
    source_verifications: tuple[SourceVerification, ...] = ()
    projection_digest: str = ""

    def __post_init__(self) -> None:
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise EvidenceProjectionError(
                "unknown_evidence_status",
                f"evidence_status {self.evidence_status!r} is not one of {list(EVIDENCE_STATUSES)}",
            )
        if self.overall_claim_status not in (
            "supported",
            "refuted",
            "inconclusive",
            "not_evaluated",
        ):
            raise EvidenceProjectionError(
                "unknown_claim_status",
                f"overall claim status {self.overall_claim_status!r} is not frozen",
            )
        if not self.projection_digest:
            object.__setattr__(self, "projection_digest", _content_digest(self._body()))

    def _body(self) -> dict[str, Any]:
        return {
            "schema": EVIDENCE_PROJECTION_SCHEMA,
            "contract_version": EVIDENCE_PROJECTION_VERSION,
            "research": {
                "principal_id": self.identity.principal_id,
                "project_id": self.identity.project_id,
                "research_id": self.identity.research_id,
                "session_id": self.identity.session_id,
                "round": self.round,
            },
            "task_ref": {
                "project_id": self.task_ref.project_id,
                "task_id": self.task_ref.task_id,
                "job_id": self.task_ref.job_id,
                "attempt": self.task_ref.attempt,
                "release": {
                    "release_id": self.task_ref.release_id,
                    "release_digest": self.task_ref.release_digest,
                },
            },
            "report": {
                "report_id": self.report_id,
                "digest": self.report_digest,
                "schema_version": self.report_schema_version,
                "subject_id": self.subject_id,
            },
            "execution_terminal_state": self.execution_terminal_state,
            "qualification": self.qualification,
            "evidence_status": self.evidence_status,
            "claims": [claim.to_dict() for claim in self.claims],
            "overall_claim_status": self.overall_claim_status,
            "blocking_missing_checks": list(self.blocking_missing_checks),
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "limitations": list(self.limitations),
            "provenance_digest": self.provenance_digest,
            "normal_reuse": self.normal_reuse,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen wire shape (body + projection digest)."""
        document = self._body()
        document["projection_digest"] = self.projection_digest
        return document

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> EvidenceView:
        """Rebuild a view from its wire shape (digest re-verified)."""
        if not isinstance(payload, Mapping):
            raise EvidenceProjectionError("invalid_evidence_view", "the view must be an object")
        research = payload.get("research")
        if not isinstance(research, Mapping):
            raise EvidenceProjectionError("invalid_evidence_view", "research identity is required")
        identity = ResearchIdentity(
            principal_id=str(research.get("principal_id") or ""),
            project_id=str(research.get("project_id") or ""),
            research_id=str(research.get("research_id") or ""),
            session_id=str(research.get("session_id") or ""),
        )
        task_ref = TaskRef.from_dict(payload.get("task_ref") or {})
        report = payload.get("report")
        if not isinstance(report, Mapping):
            raise EvidenceProjectionError("invalid_evidence_view", "report reference is required")
        claims = tuple(
            ClaimProjection(
                claim_id=str(row.get("claim_id") or ""),
                status=str(row.get("status") or ""),
                validator_id=str(row.get("validator_id") or ""),
                validator_version=str(row.get("validator_version") or ""),
                source_ref_count=int(row.get("source_ref_count") or 0),
                blocking_missing_checks=tuple(row.get("blocking_missing_checks") or ()),
            )
            for row in payload.get("claims") or ()
            if isinstance(row, Mapping)
        )
        evidence_refs = tuple(
            EvidenceRef(
                evidence_id=str(row.get("evidence_id") or ""),
                kind=str(row.get("kind") or ""),
                sha256=str(row.get("sha256") or ""),
                relative_path=row.get("relative_path"),
            )
            for row in payload.get("evidence_refs") or ()
            if isinstance(row, Mapping)
        )
        view = cls(
            identity=identity,
            round=int(payload.get("research", {}).get("round", 0)),  # type: ignore[union-attr]
            task_ref=task_ref,
            report_id=str(report.get("report_id") or ""),
            report_digest=str(report.get("digest") or ""),
            report_schema_version=int(report.get("schema_version") or 0),
            subject_id=str(report.get("subject_id") or ""),
            execution_terminal_state=str(payload.get("execution_terminal_state") or ""),
            qualification=str(payload.get("qualification") or ""),
            evidence_status=str(payload.get("evidence_status") or ""),
            claims=claims,
            overall_claim_status=str(payload.get("overall_claim_status") or ""),
            blocking_missing_checks=tuple(payload.get("blocking_missing_checks") or ()),
            evidence_refs=evidence_refs,
            limitations=tuple(payload.get("limitations") or ()),
            provenance_digest=str(payload.get("provenance_digest") or ""),
            normal_reuse=bool(payload.get("normal_reuse", False)),
        )
        declared = str(payload.get("projection_digest") or "")
        if declared and declared != view.projection_digest:
            raise EvidenceProjectionError(
                "projection_digest_mismatch",
                f"declared {declared!r} != recomputed {view.projection_digest!r}",
            )
        return view


def verify_attribution(
    view: EvidenceView,
    identity: ResearchIdentity,
    round_index: int,
    task_ref: TaskRef | None = None,
) -> None:
    """Reject a view that names a foreign project/research/round/attempt."""
    if (
        view.identity.project_id != identity.project_id
        or view.task_ref.project_id != identity.project_id
    ):
        raise EvidenceProjectionError(
            "foreign_project",
            f"view project {view.task_ref.project_id!r} != session project {identity.project_id!r}",
        )
    if view.identity.research_id != identity.research_id:
        raise EvidenceProjectionError(
            "foreign_research",
            f"view research {view.identity.research_id!r} != "
            f"session research {identity.research_id!r}",
        )
    if view.round != round_index:
        raise EvidenceProjectionError(
            "foreign_round", f"view round {view.round!r} != session round {round_index!r}"
        )
    if task_ref is not None and view.task_ref.identity() != task_ref.identity():
        raise EvidenceProjectionError(
            "foreign_task_ref",
            f"view task {view.task_ref.to_dict()} != bound task {task_ref.to_dict()}",
        )


def _content_digest(document: Mapping[str, Any]) -> str:
    """Canonical content digest of a projection document.

    The same canonicalisation as ``science_binding.document_digest`` (canonical
    key order, compact separators); the equality is asserted by
    ``tests/test_acp2_evidence_projection.py`` so the two cannot drift.
    """
    try:
        body = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceProjectionError("document_not_json", str(exc)) from exc
    return "sha256:" + sha256_bytes(body)


def _require_digest(value: Any, label: str) -> str:
    text = str(value or "")
    if _DIGEST_RE.match(text) is None:
        raise EvidenceProjectionError(
            "not_digest_bound", f"{label} must be sha256:<64 lowercase hex>"
        )
    return text


def _receipt_state(receipt: Mapping[str, Any]) -> str:
    state = str(receipt.get("state") or "")
    if state not in RECEIPT_STATES:
        raise EvidenceProjectionError(
            "invalid_receipt", f"receipt state {state!r} is not one of {sorted(RECEIPT_STATES)}"
        )
    return state


def _task_result_qualification(task_result: Mapping[str, Any]) -> tuple[str, bool]:
    if task_result.get("schema") != worker.TASK_RESULT_SCHEMA:
        raise EvidenceProjectionError(
            "invalid_task_result",
            f"task_result schema {task_result.get('schema')!r} != {worker.TASK_RESULT_SCHEMA!r}",
        )
    qualification = str(task_result.get("qualification") or "")
    evidence_status_for_qualification(qualification)
    normal_reuse = task_result.get("normal_reuse")
    return qualification, normal_reuse is True


def _verified_task_result_digest(task_result: Mapping[str, Any]) -> str:
    declared = _require_digest(task_result.get("content_digest"), "task_result.content_digest")
    body = {key: value for key, value in task_result.items() if key != "content_digest"}
    recomputed = worker.task_result_content_digest(body)
    if recomputed != declared:
        raise EvidenceProjectionError(
            "task_result_not_digest_bound",
            f"content_digest {declared!r} != recomputed {recomputed!r}",
        )
    return declared


def _assert_receipt_binds(
    task_ref: TaskRef,
    receipt: Mapping[str, Any],
    task_result_digest: str,
) -> str:
    job_id = str(receipt.get("job_id") or receipt.get("task_id") or "")
    if job_id != task_ref.task_id:
        raise EvidenceProjectionError(
            "foreign_job", f"receipt job {job_id!r} != task {task_ref.task_id!r}"
        )
    attempt = receipt.get("attempt")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt != task_ref.attempt:
        raise EvidenceProjectionError(
            "foreign_attempt", f"receipt attempt {attempt!r} != task attempt {task_ref.attempt!r}"
        )
    if str(receipt.get("release_id") or "") != task_ref.release_id:
        raise EvidenceProjectionError(
            "foreign_release",
            f"receipt release {receipt.get('release_id')!r} != "
            f"task release {task_ref.release_id!r}",
        )
    if str(receipt.get("release_digest") or "") != task_ref.release_digest:
        raise EvidenceProjectionError(
            "foreign_release_digest",
            f"receipt release digest {receipt.get('release_digest')!r} != "
            f"task release digest {task_ref.release_digest!r}",
        )
    digests = receipt.get("evidence_digests")
    if not isinstance(digests, Mapping):
        raise EvidenceProjectionError(
            "receipt_not_digest_bound", "the receipt carries no evidence_digests mapping"
        )
    if str(digests.get("task_result_digest") or "") != task_result_digest:
        raise EvidenceProjectionError(
            "receipt_not_digest_bound",
            f"receipt task_result_digest {digests.get('task_result_digest')!r} != "
            f"task_result content digest {task_result_digest!r}",
        )
    return _receipt_state(receipt)


def _evidence_refs(rows: Iterable[SourceVerification]) -> tuple[EvidenceRef, ...]:
    """Digest-bound evidence refs, deduplicated by content."""
    refs: dict[str, EvidenceRef] = {}
    for row in rows:
        sha256 = "sha256:" + row.actual_digest
        evidence_id = "art-" + row.actual_digest[:16]
        if _EVIDENCE_ID_RE.match(evidence_id) is None:
            raise EvidenceProjectionError(
                "invalid_evidence_id", f"derived evidence id {evidence_id!r} is not addressable"
            )
        if evidence_id in refs:
            continue
        refs[evidence_id] = EvidenceRef(
            evidence_id=evidence_id,
            kind=row.role or "claim_source",
            sha256=sha256,
            relative_path=row.artifact_path,
        )
    return tuple(refs[key] for key in sorted(refs))


def _blocking_missing_checks(report: EvidenceReport) -> tuple[str, ...]:
    """Explicit ``claim/check`` rows; a blocking check never disappears."""
    rows: list[str] = []
    for claim in report.claims:
        for check in claim.missing_checks:
            if check.blocking:
                rows.append(f"{claim.claim_id}/{check.check_id}")
    return tuple(rows)


def _report_provenance_digest(report: EvidenceReport) -> str:
    digest = report.provenance_digest
    if digest is None:
        return ""
    return "sha256:" + digest.value


def project_evidence(
    *,
    identity: ResearchIdentity,
    round_index: int,
    task_ref: TaskRef | Mapping[str, Any],
    receipt: Mapping[str, Any],
    task_result: Mapping[str, Any],
    report: bytes | bytearray | str,
    report_digest: str,
    read_root: str | Path,
    step_outcomes: Sequence[Mapping[str, Any]] = (),
    publication_status: str = "",
) -> EvidenceView:
    """Project one attempt into the single evidence view.

    Args:
        identity: The server-resolved research identity (M1-02).
        round_index: Research round the evidence belongs to.
        task_ref: The normalised task reference, or its raw platform mapping.
        receipt: ``execution_receipt.json`` as read by the caller.
        task_result: ``task_result.json`` (content digest re-verified here).
        report: The **exact bytes** of the evidence report artifact.
        report_digest: Declared digest of those bytes.
        read_root: Controlled result root every claim source is verified under.
        step_outcomes: Optional per-step ``{status, publish_status}`` rows.
        publication_status: Optional step/DAG publication status.

    Returns:
        The projected :class:`EvidenceView`.

    Raises:
        EvidenceProjectionError: With a stable code for every refusal
            (foreign project/job/attempt/release, a non digest-bound value, a
            model conclusion used as evidence, an unreadable report).
    """
    if isinstance(round_index, bool) or not isinstance(round_index, int) or round_index < 0:
        raise EvidenceProjectionError("invalid_round", "round must be a non-negative integer")
    normalized = task_ref if isinstance(task_ref, TaskRef) else TaskRef.from_dict(task_ref)
    if normalized.project_id != identity.project_id:
        raise EvidenceProjectionError(
            "foreign_project",
            f"task project {normalized.project_id!r} != session project {identity.project_id!r}",
        )

    task_result_digest = _verified_task_result_digest(task_result)
    qualification, normal_reuse = _task_result_qualification(task_result)
    receipt_state = _assert_receipt_binds(normalized, receipt, task_result_digest)

    declared_report_digest = _require_digest(report_digest, "report_digest")
    if not isinstance(report, (bytes, bytearray, str)):
        raise EvidenceProjectionError(
            "report_not_digest_bound",
            "an evidence report is admitted as its exact bytes; a parsed mapping is not evidence",
        )
    raw = bytes(report) if isinstance(report, (bytes, bytearray)) else str(report).encode("utf-8")
    actual_report_digest = "sha256:" + sha256_bytes(raw)
    if actual_report_digest != declared_report_digest:
        raise EvidenceProjectionError(
            "report_digest_mismatch",
            f"declared {declared_report_digest!r} != actual {actual_report_digest!r}",
        )
    document = _report_mapping(raw)
    report_model = parse_evidence_report(document)
    if report_model.execution.terminal_state not in EXECUTION_TERMINAL_STATES:
        raise EvidenceProjectionError(
            "invalid_execution_state",
            f"execution terminal state {report_model.execution.terminal_state!r} is not frozen",
        )
    expected_report_state = "failed" if receipt_state == "blocked" else receipt_state
    if report_model.execution.terminal_state != expected_report_state:
        raise EvidenceProjectionError(
            "execution_state_conflict",
            f"report execution {report_model.execution.terminal_state!r} != "
            f"receipt state {receipt_state!r}",
        )

    try:
        verifications = verify_claim_sources(report_model, Path(read_root))
    except (
        EvidenceDigestMismatchError,
        EvidencePathError,
        EvidenceSourceUnavailableError,
        EvidenceContractError,
    ) as exc:
        code = (
            "evidence_digest_mismatch"
            if isinstance(exc, EvidenceDigestMismatchError)
            else "evidence_source_unavailable"
            if isinstance(exc, EvidenceSourceUnavailableError)
            else "evidence_path_refused"
        )
        raise EvidenceProjectionError(code, str(exc)) from exc

    step_statuses = tuple(
        evidence_status_for_step(str(row.get("status") or ""), str(row.get("publish_status") or ""))
        for row in step_outcomes
    )
    published = (
        evidence_status_for_step("completed", str(publication_status)) if publication_status else ""
    )
    evidence_status = combine_evidence_status(
        evidence_status_for_qualification(qualification),
        *step_statuses,
        published,
    )

    claims = tuple(ClaimProjection.from_claim(claim) for claim in report_model.claims)
    return EvidenceView(
        identity=identity,
        round=round_index,
        task_ref=normalized,
        report_id=report_model.report_id,
        report_digest=declared_report_digest,
        report_schema_version=report_model.schema_version,
        subject_id=report_model.subject.subject_id,
        execution_terminal_state=report_model.execution.terminal_state,
        qualification=qualification,
        evidence_status=evidence_status,
        claims=claims,
        overall_claim_status=report_model.overall_claim_status,
        blocking_missing_checks=_blocking_missing_checks(report_model),
        evidence_refs=_evidence_refs(verifications),
        limitations=tuple(limit.description for limit in report_model.limitations),
        provenance_digest=_report_provenance_digest(report_model),
        normal_reuse=normal_reuse,
        source_verifications=verifications,
    )


# ---------------------------------------------------------------------------
# Independent research decision
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResearchDecision:
    """The independent decision record of one research round.

    It is not a task/job status and it is not a scientific claim: it is the
    auditable statement "given this digest-bound view, the round decides X".
    There is no free-text field, so a model narrative can never be published as
    the reason for a decision.
    """

    project_id: str
    research_id: str
    round: int
    decision: str
    task_ref: TaskRef
    evidence_status: str
    overall_claim_status: str
    blocking_missing_checks: tuple[str, ...]
    evidence_refs: tuple[EvidenceRef, ...]
    rationale_codes: tuple[str, ...]
    projection_digest: str

    def __post_init__(self) -> None:
        if self.decision not in DECISIONS:
            raise ResearchDecisionError(
                "unknown_decision",
                f"decision {self.decision!r} is not one of {list(DECISIONS)}",
            )
        if not self.evidence_refs:
            raise ResearchDecisionError(
                "evidence_ref_required",
                "a decision must cite at least one digest-bound evidence ref",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": RESEARCH_DECISION_SCHEMA,
            "project_id": self.project_id,
            "research_id": self.research_id,
            "round": self.round,
            "decision": self.decision,
            "task_ref": self.task_ref.to_dict(),
            "evidence": {
                "status": self.evidence_status,
                "overall_claim_status": self.overall_claim_status,
                "blocking_missing_checks": list(self.blocking_missing_checks),
            },
            "evidence_refs": [ref.to_dict() for ref in self.evidence_refs],
            "rationale_codes": list(self.rationale_codes),
            "projection_digest": self.projection_digest,
        }


def _rationale_codes(view: EvidenceView) -> tuple[str, ...]:
    codes: list[str] = [
        "execution_completed"
        if view.execution_terminal_state == "completed"
        else "execution_not_completed",
        f"evidence_{view.evidence_status}",
        f"claim_{view.overall_claim_status}",
    ]
    if view.blocking_missing_checks:
        codes.append("blocking_missing_check")
    codes.append("digest_bound_evidence" if view.evidence_refs else "no_digest_bound_evidence")
    return tuple(code for code in DECISION_RATIONALE_CODES if code in codes)


def validate_research_decision(view: EvidenceView, decision: str) -> None:
    """Enforce the frozen decision policy against one view.

    ``accept`` is the only decision with preconditions, evaluated in the frozen
    order of :data:`ACCEPT_PRECONDITION_CODES`:

    1. the execution must be ``completed`` — a returned value is not an
       accepted result;
    2. the evidence status must be ``complete`` — ``partial``/``incomplete``/
       ``failed`` cannot be accepted;
    3. no blocking missing check may remain (reported before the generic claim
       rule so the actionable cause is explicit);
    4. the overall claim status must be ``supported`` — ``refuted``,
       ``inconclusive`` and ``not_evaluated`` are explicit and never upgraded;
    5. at least one digest-bound evidence ref must be cited.

    ``revise``/``reject``/``defer``/``escalate`` stay always available: they are
    the honest answers to incomplete, refuted or inconclusive evidence.
    """
    if decision not in DECISIONS:
        raise ResearchDecisionError(
            "unknown_decision", f"decision {decision!r} is not one of {list(DECISIONS)}"
        )
    if not view.evidence_refs:
        raise ResearchDecisionError(
            "evidence_ref_required",
            "a decision must cite at least one digest-bound evidence ref",
        )
    if decision != "accept":
        return
    if view.execution_terminal_state != "completed":
        raise ResearchDecisionError(
            "execution_not_completed",
            f"execution terminal state {view.execution_terminal_state!r} cannot be accepted",
        )
    if view.evidence_status != "complete":
        raise ResearchDecisionError(
            "evidence_not_complete",
            f"evidence status {view.evidence_status!r} cannot be accepted",
        )
    if view.blocking_missing_checks:
        raise ResearchDecisionError(
            "blocking_missing_check",
            f"blocking missing checks remain: {list(view.blocking_missing_checks)}",
        )
    if view.overall_claim_status != "supported":
        raise ResearchDecisionError(
            "claim_status_not_supported",
            f"overall claim status {view.overall_claim_status!r} cannot be accepted",
        )


def decide_evidence(view: EvidenceView, decision: str) -> ResearchDecision:
    """Build the independent decision record for *view* (policy enforced)."""
    validate_research_decision(view, decision)
    return ResearchDecision(
        project_id=view.identity.project_id,
        research_id=view.identity.research_id,
        round=view.round,
        decision=decision,
        task_ref=view.task_ref,
        evidence_status=view.evidence_status,
        overall_claim_status=view.overall_claim_status,
        blocking_missing_checks=view.blocking_missing_checks,
        evidence_refs=view.evidence_refs,
        rationale_codes=_rationale_codes(view),
        projection_digest=view.projection_digest,
    )


def decision_from_dict(
    payload: Mapping[str, Any], *, view: EvidenceView | None = None
) -> ResearchDecision:
    """Rebuild a decision record from its wire shape.

    The envelope is **closed**: there is no field a model narrative could
    occupy, and an unknown field (``reason``, ``summary``, …) is a refusal.
    When *view* is supplied it is the authority: a decision that cites a
    foreign project/research/round/task/projection is refused.
    """
    if not isinstance(payload, Mapping):
        raise ResearchDecisionError("invalid_decision", "the decision must be an object")
    unknown = sorted(set(payload) - _DECISION_FIELDS)
    if unknown:
        raise ResearchDecisionError(
            "decision_field_not_allowed",
            f"a decision carries rationale codes, not free text: {unknown}",
        )
    schema = str(payload.get("schema") or RESEARCH_DECISION_SCHEMA)
    if schema != RESEARCH_DECISION_SCHEMA:
        raise ResearchDecisionError(
            "invalid_decision", f"decision schema {schema!r} != {RESEARCH_DECISION_SCHEMA!r}"
        )
    task_ref = TaskRef.from_dict(payload.get("task_ref") or {})
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), Mapping) else {}
    unknown_evidence = sorted(set(evidence) - _DECISION_EVIDENCE_FIELDS)
    if unknown_evidence:
        raise ResearchDecisionError(
            "decision_field_not_allowed", f"unknown evidence fields: {unknown_evidence}"
        )
    evidence_status = str(evidence.get("status") or "")
    if evidence_status not in EVIDENCE_STATUSES:
        raise ResearchDecisionError(
            "unknown_evidence_status",
            f"evidence status {evidence_status!r} is not one of {list(EVIDENCE_STATUSES)}",
        )
    overall = str(evidence.get("overall_claim_status") or "")
    if overall not in _CLAIM_STATUSES:
        raise ResearchDecisionError(
            "unknown_claim_status", f"overall claim status {overall!r} is not frozen"
        )
    rationale_codes = tuple(str(code) for code in payload.get("rationale_codes") or ())
    unknown_codes = sorted(set(rationale_codes) - set(DECISION_RATIONALE_CODES))
    if unknown_codes:
        raise ResearchDecisionError(
            "decision_field_not_allowed", f"unknown rationale codes: {unknown_codes}"
        )
    evidence_refs = tuple(
        EvidenceRef(
            evidence_id=str(row.get("evidence_id") or ""),
            kind=str(row.get("kind") or ""),
            sha256=str(row.get("sha256") or ""),
            relative_path=row.get("relative_path"),
        )
        for row in payload.get("evidence_refs") or ()
        if isinstance(row, Mapping)
    )
    decision = ResearchDecision(
        project_id=str(payload.get("project_id") or ""),
        research_id=str(payload.get("research_id") or ""),
        round=int(payload.get("round") or 0),
        decision=str(payload.get("decision") or ""),
        task_ref=task_ref,
        evidence_status=evidence_status,
        overall_claim_status=overall,
        blocking_missing_checks=tuple(evidence.get("blocking_missing_checks") or ()),
        evidence_refs=evidence_refs,
        rationale_codes=rationale_codes,
        projection_digest=str(payload.get("projection_digest") or ""),
    )
    if view is not None:
        if (
            decision.project_id != view.identity.project_id
            or decision.task_ref.project_id != view.identity.project_id
        ):
            raise ResearchDecisionError(
                "foreign_project",
                f"decision project {decision.project_id!r} != "
                f"view project {view.identity.project_id!r}",
            )
        if decision.research_id != view.identity.research_id:
            raise ResearchDecisionError(
                "foreign_research",
                f"decision research {decision.research_id!r} != "
                f"view research {view.identity.research_id!r}",
            )
        if decision.round != view.round:
            raise ResearchDecisionError(
                "foreign_round", f"decision round {decision.round!r} != view round {view.round!r}"
            )
        if decision.task_ref.identity() != view.task_ref.identity():
            raise ResearchDecisionError(
                "foreign_task_ref",
                f"decision task {decision.task_ref.to_dict()} != "
                f"view task {view.task_ref.to_dict()}",
            )
        if decision.projection_digest != view.projection_digest:
            raise ResearchDecisionError(
                "foreign_projection",
                f"decision projection {decision.projection_digest!r} != "
                f"view projection {view.projection_digest!r}",
            )
        validate_research_decision(view, decision.decision)
    return decision
