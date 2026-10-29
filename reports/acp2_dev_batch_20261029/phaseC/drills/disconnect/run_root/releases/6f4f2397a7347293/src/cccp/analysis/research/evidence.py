"""ACP2 evidence-report science type (``evidence.schema.json``, major v1).

This module assembles and validates the frozen :class:`EvidenceReport`
contract from *completed* scientific steps.  It deliberately mirrors
``docs/reference/next-change/acp2-contracts/science/evidence.schema.json`` and
the semantics frozen in ``evidence-semantics.md``.

Two separations are structural rather than advisory:

* **Execution is not a claim.**  ``terminal_state`` / ``exit_code`` / ``argv``
  live only on :class:`ExecutionFacts`; a :class:`Claim` has no execution
  field at all.  A completed process may therefore carry an ``inconclusive``
  or ``refuted`` claim and no code path maps ``completed`` to ``supported``.
* **Model text is not a validator.**  :class:`EvidenceValidator` accepts only
  the ``check.`` / ``software.`` / ``human.`` namespaces, and
  :class:`ModelNarrative` is explicitly non-evidence.  A natural-language
  conclusion produced by a model can never become a validated fact.

A ``supported`` or ``refuted`` claim requires both an admissible validator
identity (id + version) and at least one digested :class:`ArtifactSourceRef`.
:func:`assemble_claim` applies the honest downgrade rule: when a completed
step produced no validator or no digested source, the claim becomes
``inconclusive`` with an explicit missing check instead of being asserted.

The module is pure: standard library only, no task/QC/platform import and no
subprocess.  Digests use the bare lowercase sha256 value defined by
``cccp.analysis.identity.content_sha256`` (never a ``sha256:`` prefix inside
the JSON contract).

Two boundaries keep this module independent of the execution layer:

* :func:`artifact_source_ref_from_calculation_artifact` adapts the *runtime*
  ``cccp.calculation.contracts.ArtifactRef`` **into** this module's
  :class:`ArtifactSourceRef`.  The calculation type is imported lazily inside
  the function so importing this module never loads ``cccp.calculation``; the
  adapter is strictly one-directional and never re-defines the runtime type.
  A missing checksum is rejected instead of being filled with a fabricated
  digest, and a caller must supply ``role``/``units`` explicitly because the
  runtime record carries no scientific role.
* :func:`read_historical_evidence` reads an older payload *without* coercion:
  the payload is preserved verbatim, missing fields stay missing, unknown
  fields are reported and retained, and the record is explicitly not eligible
  for execution or recovery.  Only :meth:`EvidenceReport.from_dict` produces a
  v1 object usable for new work.

Author: QCcalc Team
"""

from __future__ import annotations

import copy
import hashlib
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, get_args

from cccp.analysis.research._common import (
    ScienceContractError,
    canonical_json,
    reject_unknown_keys,
    require_int,
    require_mapping,
    require_sequence,
    require_string,
)

logger = logging.getLogger(__name__)

__all__ = [
    "APPLICABILITIES",
    "CLAIM_STATUSES",
    "DIGEST_KEYS",
    "DIGEST_PATTERN",
    "EVIDENCE_SCHEMA_VERSION",
    "MISSING_CHECK_IDS",
    "SUBJECT_KINDS",
    "TERMINAL_STATES",
    "VALIDATOR_KINDS",
    "VALIDATOR_ID_PATTERN",
    "VALIDATOR_VERSION_PATTERN",
    "ArtifactSourceRef",
    "Claim",
    "ClaimDraft",
    "ClaimStatus",
    "ClaimEvidenceError",
    "ClaimScope",
    "Digest",
    "EvidenceContractError",
    "EvidenceDigestMismatchError",
    "EvidenceMissingCheck",
    "EvidencePathError",
    "EvidenceReport",
    "EvidenceSourceUnavailableError",
    "EvidenceValidator",
    "ExecutionFacts",
    "FailedCandidate",
    "HistoricalEvidenceRecord",
    "Limitation",
    "ModelConclusionError",
    "ModelNarrative",
    "ReportLimits",
    "SourceVerification",
    "Subject",
    "UnknownClaimStatusError",
    "UnsupportedEvidenceSchemaVersionError",
    "artifact_source_ref_from_calculation_artifact",
    "assemble_claim",
    "assemble_evidence_report",
    "canonical_evidence_json",
    "claim_draft_from_assessment",
    "digest_from_checksum",
    "evidence_report_from_dict",
    "read_historical_evidence",
    "reject_model_conclusion_as_evidence",
    "unknown_evidence_field_paths",
    "verify_artifact_digest",
    "verify_claim_sources",
]

# ── frozen vocabulary (mirrors evidence.schema.json) ───────────────────────

EVIDENCE_SCHEMA_VERSION: Final = 1

ClaimStatus = Literal["supported", "refuted", "inconclusive", "not_evaluated"]
CLAIM_STATUSES: Final[frozenset[str]] = frozenset(get_args(ClaimStatus))

APPLICABILITIES: Final[frozenset[str]] = frozenset(
    {
        "universal",
        "system_specific",
        "ensemble_specific",
        "conformer_specific",
        "condition_specific",
    }
)
SUBJECT_KINDS: Final[frozenset[str]] = frozenset(
    {"research_record", "calculation_result", "recipe_run", "artifact_set"}
)
TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {"completed", "failed", "cancelled", "not_started"}
)
VALIDATOR_KINDS: Final[frozenset[str]] = frozenset({"deterministic", "software", "human_review"})
_VALIDATOR_KIND_NAMESPACE: Final[dict[str, str]] = {
    "deterministic": "check.",
    "software": "software.",
    "human_review": "human.",
}

#: Frozen missing-check vocabulary.  A new id requires a ``schema_version``
#: bump; prose is never accepted as a check id.
MISSING_CHECK_IDS: Final[frozenset[str]] = frozenset(
    {
        "artifact_missing",
        "artifact_digest_mismatch",
        "partial_output",
        "missing_irc",
        "irc_not_bidirectional",
        "imaginary_mode_missing",
        "imaginary_mode_count",
        "geometry_not_stationary",
        "frequency_not_computed",
        "convergence_not_met",
        "electronic_state_unresolved",
        "basis_incomplete",
        "temperature_unspecified",
        "standard_state_unspecified",
        "reference_state_unspecified",
        "method_identity_missing",
        "software_version_missing",
        "validator_unavailable",
        "external_artifact_unavailable",
        "run_key_unavailable",
        "not_executed",
    }
)

DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
VALIDATOR_ID_PATTERN: Final = re.compile(
    r"^(?P<namespace>check|software|human)\.[a-z0-9][a-z0-9_.-]*$"
)
VALIDATOR_VERSION_PATTERN: Final = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
SCIENCE_IDENTITY_PATTERN: Final = re.compile(r"^v2:[0-9a-f]{32}$")
_TIMESTAMP_PATTERN: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)

_REPORT_KEYS: Final = frozenset(
    {
        "schema_version",
        "report_id",
        "subject",
        "execution",
        "claims",
        "limitations",
        "provenance_digest",
    }
)
_CLAIM_KEYS: Final = frozenset(
    {
        "claim_id",
        "statement",
        "status",
        "scope",
        "source_refs",
        "missing_checks",
        "validator",
        "limitations",
    }
)
_SCOPE_KEYS: Final = frozenset({"subject", "applicability", "conditions", "not_in_scope"})
_SOURCE_REF_KEYS: Final = frozenset({"artifact_path", "digest", "role", "units", "quantity"})
_VALIDATOR_KEYS: Final = frozenset({"validator_id", "validator_version", "kind", "description"})
_MISSING_CHECK_KEYS: Final = frozenset({"check_id", "reason", "blocking"})
_EXECUTION_KEYS: Final = frozenset(
    {
        "terminal_state",
        "argv",
        "exit_code",
        "started_at",
        "finished_at",
        "stdout_path",
        "stderr_path",
    }
)
_SUBJECT_KEYS: Final = frozenset(
    {"subject_id", "subject_kind", "description", "input_digests", "science_identity"}
)
_LIMITATION_KEYS: Final = frozenset({"limitation_id", "description", "affected_claim_ids"})
DIGEST_KEYS: Final = frozenset({"algorithm", "value"})


# ── typed errors ───────────────────────────────────────────────────────────


class EvidenceContractError(ScienceContractError):
    """Base class for a rejected ACP2 evidence payload."""


class UnsupportedEvidenceSchemaVersionError(EvidenceContractError):
    """The report declares a schema major version this module cannot read."""


class UnknownClaimStatusError(EvidenceContractError):
    """A status outside the frozen four-valued vocabulary was supplied."""


class ClaimEvidenceError(EvidenceContractError):
    """A claim asserts support/refutation without admissible evidence."""


class ModelConclusionError(EvidenceContractError):
    """Model-generated narrative was offered as validated evidence."""


class EvidencePathError(EvidenceContractError):
    """An artifact path is absolute or escapes the declared evidence root."""


class EvidenceSourceUnavailableError(EvidenceContractError):
    """A declared claim source is absent or unreadable below the evidence root."""


class EvidenceDigestMismatchError(EvidenceContractError):
    """A claim source exists but its bytes disagree with the declared digest."""


# ── small validators ───────────────────────────────────────────────────────


def _mapping(value: Any, where: str) -> dict[str, Any]:
    try:
        return require_mapping(value, where)
    except ScienceContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _sequence(value: Any, where: str) -> list[Any]:
    try:
        return require_sequence(value, where)
    except ScienceContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _string(value: Any, where: str) -> str:
    try:
        return require_string(value, where)
    except ScienceContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _int(value: Any, where: str) -> int:
    try:
        return require_int(value, where)
    except ScienceContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _optional_string(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return _string(value, where)


def _optional_int(value: Any, where: str) -> int | None:
    if value is None:
        return None
    return _int(value, where)


def _require_keys(mapping: Mapping[str, Any], required: Iterable[str], where: str) -> None:
    missing = sorted(set(required) - set(mapping))
    if missing:
        raise EvidenceContractError(f"{where}: missing required field(s) {missing}")


def _reject_unknown(mapping: Mapping[str, Any], allowed: Iterable[str], where: str) -> None:
    try:
        reject_unknown_keys(mapping, allowed, where)
    except ScienceContractError as exc:
        raise EvidenceContractError(str(exc)) from exc


def _relative_artifact_path(value: Any, where: str) -> str:
    path = _string(value, where)
    pure = path.replace("\\", "/")
    if pure.startswith("/") or re.match(r"^[A-Za-z]:/", pure):
        raise EvidencePathError(f"{where}: artifact path must be relative, got {path!r}")
    parts = [part for part in pure.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise EvidencePathError(f"{where}: artifact path must not traverse parents, got {path!r}")
    return path


# ── digest ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Digest:
    """A sha256 content digest using the frozen bare-lowercase value form."""

    value: str
    algorithm: str = "sha256"

    def __post_init__(self) -> None:
        if self.algorithm != "sha256":
            raise EvidenceContractError(
                f"digest.algorithm must be 'sha256', got {self.algorithm!r}"
            )
        if not isinstance(self.value, str) or DIGEST_PATTERN.fullmatch(self.value) is None:
            raise EvidenceContractError(
                "digest.value must be a bare 64-character lowercase sha256 hex string"
            )

    @classmethod
    def from_bytes(cls, payload: bytes) -> Digest:
        """Return the sha256 digest of *payload*."""
        return cls(hashlib.sha256(payload).hexdigest())

    @classmethod
    def from_dict(cls, payload: Any, where: str = "digest") -> Digest:
        """Read a frozen digest object."""
        data = _mapping(payload, where)
        _reject_unknown(data, DIGEST_KEYS, where)
        _require_keys(data, ("algorithm", "value"), where)
        return cls(
            value=_string(data["value"], f"{where}.value"),
            algorithm=_string(data["algorithm"], f"{where}.algorithm"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped digest object."""
        return {"algorithm": self.algorithm, "value": self.value}


# ── claim scope, sources, validator, missing checks ────────────────────────


@dataclass(frozen=True, slots=True)
class ClaimScope:
    """The scientific scope of one claim."""

    subject: str
    applicability: str
    conditions: str | None = None
    not_in_scope: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _string(self.subject, "scope.subject"))
        if self.applicability not in APPLICABILITIES:
            raise EvidenceContractError(
                f"scope.applicability must be one of {sorted(APPLICABILITIES)}, "
                f"got {self.applicability!r}"
            )
        object.__setattr__(
            self, "conditions", _optional_string(self.conditions, "scope.conditions")
        )
        not_in_scope = tuple(
            _string(item, f"scope.not_in_scope[{index}]")
            for index, item in enumerate(self.not_in_scope)
        )
        object.__setattr__(self, "not_in_scope", not_in_scope)

    @classmethod
    def from_dict(cls, payload: Any, where: str = "claim.scope") -> ClaimScope:
        """Read one claim scope."""
        data = _mapping(payload, where)
        _reject_unknown(data, _SCOPE_KEYS, where)
        _require_keys(data, ("subject", "applicability"), where)
        raw_not_in_scope = data.get("not_in_scope", [])
        return cls(
            subject=_string(data["subject"], f"{where}.subject"),
            applicability=_string(data["applicability"], f"{where}.applicability"),
            conditions=_optional_string(data.get("conditions"), f"{where}.conditions"),
            not_in_scope=tuple(
                _string(item, f"{where}.not_in_scope[{index}]")
                for index, item in enumerate(_sequence(raw_not_in_scope, f"{where}.not_in_scope"))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped scope object."""
        return {
            "subject": self.subject,
            "applicability": self.applicability,
            "conditions": self.conditions,
            "not_in_scope": list(self.not_in_scope),
        }


@dataclass(frozen=True, slots=True)
class ArtifactSourceRef:
    """One digested artifact reference supporting a claim.

    ``units`` is a required field; ``None`` is the explicit statement that the
    artifact is not a numeric quantity.  It is never omitted.
    """

    artifact_path: str
    digest: Digest
    role: str
    units: str | None
    quantity: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "artifact_path",
            _relative_artifact_path(self.artifact_path, "source_ref.artifact_path"),
        )
        if not isinstance(self.digest, Digest):
            raise EvidenceContractError("source_ref.digest must be a Digest")
        object.__setattr__(self, "role", _string(self.role, "source_ref.role"))
        if self.units is not None:
            object.__setattr__(self, "units", _string(self.units, "source_ref.units"))
        if self.quantity is not None:
            object.__setattr__(self, "quantity", _string(self.quantity, "source_ref.quantity"))

    @classmethod
    def from_dict(cls, payload: Any, where: str = "source_ref") -> ArtifactSourceRef:
        """Read one digested source reference."""
        data = _mapping(payload, where)
        _reject_unknown(data, _SOURCE_REF_KEYS, where)
        _require_keys(data, ("artifact_path", "digest", "role", "units"), where)
        return cls(
            artifact_path=_string(data["artifact_path"], f"{where}.artifact_path"),
            digest=Digest.from_dict(data["digest"], f"{where}.digest"),
            role=_string(data["role"], f"{where}.role"),
            units=_optional_string(data["units"], f"{where}.units"),
            quantity=_optional_string(data.get("quantity"), f"{where}.quantity"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped source reference."""
        document: dict[str, Any] = {
            "artifact_path": self.artifact_path,
            "digest": self.digest.to_dict(),
            "role": self.role,
            "units": self.units,
        }
        if self.quantity is not None:
            document["quantity"] = self.quantity
        return document


@dataclass(frozen=True, slots=True)
class EvidenceValidator:
    """An admissible machine-checkable or human-reviewable validator.

    There is no ``model``/``llm`` kind: a narrative answer can never occupy the
    validator slot, and the id namespace must agree with the declared kind.
    """

    validator_id: str
    validator_version: str
    kind: str
    description: str | None = None

    def __post_init__(self) -> None:
        kind = _string(self.kind, "validator.kind")
        if kind not in VALIDATOR_KINDS:
            raise ModelConclusionError(
                f"validator.kind must be one of {sorted(VALIDATOR_KINDS)}, got {kind!r}; "
                "model/LLM text is never a validator"
            )
        identifier = _string(self.validator_id, "validator.validator_id")
        match = VALIDATOR_ID_PATTERN.fullmatch(identifier)
        if match is None:
            raise ModelConclusionError(
                "validator.validator_id must be a registered check./software./human. "
                f"identifier, got {identifier!r}"
            )
        expected = _VALIDATOR_KIND_NAMESPACE[kind]
        if not identifier.startswith(expected):
            raise EvidenceContractError(
                f"validator.kind {kind!r} requires a {expected!r} id, got {identifier!r}"
            )
        version = _string(self.validator_version, "validator.validator_version")
        if VALIDATOR_VERSION_PATTERN.fullmatch(version) is None:
            raise EvidenceContractError(
                f"validator.validator_version must start with a digit and contain only "
                f"version characters, got {version!r}"
            )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "validator_id", identifier)
        object.__setattr__(self, "validator_version", version)
        object.__setattr__(
            self, "description", _optional_string(self.description, "validator.description")
        )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "claim.validator") -> EvidenceValidator:
        """Read an admissible validator."""
        data = _mapping(payload, where)
        _reject_unknown(data, _VALIDATOR_KEYS, where)
        _require_keys(data, ("validator_id", "validator_version", "kind"), where)
        return cls(
            validator_id=_string(data["validator_id"], f"{where}.validator_id"),
            validator_version=_string(data["validator_version"], f"{where}.validator_version"),
            kind=_string(data["kind"], f"{where}.kind"),
            description=_optional_string(data.get("description"), f"{where}.description"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped validator object."""
        document: dict[str, Any] = {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "kind": self.kind,
        }
        if self.description is not None:
            document["description"] = self.description
        return document


@dataclass(frozen=True, slots=True)
class EvidenceMissingCheck:
    """One missing/partial check from the frozen vocabulary."""

    check_id: str
    reason: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.check_id not in MISSING_CHECK_IDS:
            raise EvidenceContractError(
                f"missing_check.check_id {self.check_id!r} is not in the frozen vocabulary"
            )
        object.__setattr__(self, "reason", _string(self.reason, "missing_check.reason"))
        if not isinstance(self.blocking, bool):
            raise EvidenceContractError("missing_check.blocking must be a boolean")

    @classmethod
    def from_dict(cls, payload: Any, where: str = "missing_check") -> EvidenceMissingCheck:
        """Read one missing-check entry."""
        data = _mapping(payload, where)
        _reject_unknown(data, _MISSING_CHECK_KEYS, where)
        _require_keys(data, ("check_id", "reason"), where)
        blocking = data.get("blocking", True)
        if not isinstance(blocking, bool):
            raise EvidenceContractError(f"{where}.blocking must be a boolean")
        return cls(
            check_id=_string(data["check_id"], f"{where}.check_id"),
            reason=_string(data["reason"], f"{where}.reason"),
            blocking=blocking,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped missing-check entry."""
        return {"check_id": self.check_id, "reason": self.reason, "blocking": self.blocking}


@dataclass(frozen=True, slots=True)
class Limitation:
    """A report-level limitation with a stable id."""

    limitation_id: str
    description: str
    affected_claim_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "limitation_id", _string(self.limitation_id, "limitation.limitation_id")
        )
        object.__setattr__(self, "description", _string(self.description, "limitation.description"))
        affected = tuple(
            _string(item, f"limitation.affected_claim_ids[{index}]")
            for index, item in enumerate(self.affected_claim_ids)
        )
        object.__setattr__(self, "affected_claim_ids", affected)

    @classmethod
    def from_dict(cls, payload: Any, where: str = "limitation") -> Limitation:
        """Read one report limitation."""
        data = _mapping(payload, where)
        _reject_unknown(data, _LIMITATION_KEYS, where)
        _require_keys(data, ("limitation_id", "description"), where)
        raw_affected = data.get("affected_claim_ids", [])
        return cls(
            limitation_id=_string(data["limitation_id"], f"{where}.limitation_id"),
            description=_string(data["description"], f"{where}.description"),
            affected_claim_ids=tuple(
                _string(item, f"{where}.affected_claim_ids[{index}]")
                for index, item in enumerate(_sequence(raw_affected, f"{where}.affected_claim_ids"))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped limitation."""
        document: dict[str, Any] = {
            "limitation_id": self.limitation_id,
            "description": self.description,
        }
        if self.affected_claim_ids:
            document["affected_claim_ids"] = list(self.affected_claim_ids)
        return document


# ── report limits (sampling, tolerances, model boundaries, failures) ───────

_FAILED_CANDIDATE_STATUSES: Final = frozenset({"rejected", "failed", "not_attempted"})


@dataclass(frozen=True, slots=True)
class FailedCandidate:
    """One candidate that did not become evidence, with its explicit reason."""

    candidate_id: str
    stage: str
    status: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_id", _string(self.candidate_id, "failed_candidate.candidate_id")
        )
        object.__setattr__(self, "stage", _string(self.stage, "failed_candidate.stage"))
        if self.status not in _FAILED_CANDIDATE_STATUSES:
            raise EvidenceContractError(
                f"failed_candidate.status must be one of "
                f"{sorted(_FAILED_CANDIDATE_STATUSES)}, got {self.status!r}"
            )
        object.__setattr__(self, "reason", _string(self.reason, "failed_candidate.reason"))

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe failed-candidate row."""
        return {
            "candidate_id": self.candidate_id,
            "stage": self.stage,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ReportLimits:
    """The non-claim context an honest evidence report must carry.

    The frozen evidence schema has no separate limits object; this type is the
    report builder's audit record.  Its facts are also expressed in the
    report-level limitations and claim scopes so the schema document remains
    closed and self-describing.
    """

    conformation_sampling: Mapping[str, Any]
    tolerances: Mapping[str, Any]
    model_boundaries: tuple[str, ...]
    failed_candidates: tuple[FailedCandidate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "conformation_sampling", dict(self.conformation_sampling))
        object.__setattr__(self, "tolerances", dict(self.tolerances))
        boundaries = tuple(
            _string(item, f"limits.model_boundaries[{index}]")
            for index, item in enumerate(self.model_boundaries)
        )
        if not boundaries:
            raise EvidenceContractError("limits.model_boundaries must contain at least one entry")
        object.__setattr__(self, "model_boundaries", boundaries)
        object.__setattr__(self, "failed_candidates", tuple(self.failed_candidates))
        for index, candidate in enumerate(self.failed_candidates):
            if not isinstance(candidate, FailedCandidate):
                raise EvidenceContractError(
                    f"limits.failed_candidates[{index}] must be a FailedCandidate"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe audit record."""
        return {
            "conformation_sampling": dict(self.conformation_sampling),
            "tolerances": dict(self.tolerances),
            "model_boundaries": list(self.model_boundaries),
            "failed_candidates": [item.to_dict() for item in self.failed_candidates],
        }


# ── claim ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Claim:
    """One scientific claim with its four-valued status and evidence floor.

    The constructor enforces the schema's status-dependent floor:
    ``supported``/``refuted`` need a validator and a digested source;
    ``not_evaluated`` needs an explicit missing check; an ``inconclusive``
    claim with no source must explain what evidence is missing.
    """

    claim_id: str
    statement: str
    status: str
    scope: ClaimScope
    source_refs: tuple[ArtifactSourceRef, ...] = ()
    missing_checks: tuple[EvidenceMissingCheck, ...] = ()
    validator: EvidenceValidator | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "claim_id", _string(self.claim_id, "claim.claim_id"))
        object.__setattr__(self, "statement", _string(self.statement, "claim.statement"))
        if self.status not in CLAIM_STATUSES:
            raise UnknownClaimStatusError(
                f"claim {self.claim_id!r} has unknown status {self.status!r}; "
                f"the frozen vocabulary is {sorted(CLAIM_STATUSES)}"
            )
        if not isinstance(self.scope, ClaimScope):
            raise EvidenceContractError("claim.scope must be a ClaimScope")
        object.__setattr__(self, "source_refs", tuple(self.source_refs))
        object.__setattr__(self, "missing_checks", tuple(self.missing_checks))
        object.__setattr__(
            self,
            "limitations",
            tuple(_string(item, "claim.limitations[]") for item in self.limitations),
        )
        for index, source_ref in enumerate(self.source_refs):
            if not isinstance(source_ref, ArtifactSourceRef):
                raise EvidenceContractError(
                    f"claim {self.claim_id!r} source_refs[{index}] must be an ArtifactSourceRef"
                )
        for index, missing in enumerate(self.missing_checks):
            if not isinstance(missing, EvidenceMissingCheck):
                raise EvidenceContractError(
                    f"claim {self.claim_id!r} missing_checks[{index}] must be an "
                    "EvidenceMissingCheck"
                )
        if self.validator is not None and not isinstance(self.validator, EvidenceValidator):
            raise EvidenceContractError("claim.validator must be an EvidenceValidator or None")
        if self.status in ("supported", "refuted"):
            if self.validator is None:
                raise ClaimEvidenceError(
                    f"claim {self.claim_id!r} cannot be {self.status!r} without a validator "
                    "id and version"
                )
            if not self.source_refs:
                raise ClaimEvidenceError(
                    f"claim {self.claim_id!r} cannot be {self.status!r} without at least one "
                    "digested source reference"
                )
            if any(check.blocking for check in self.missing_checks):
                raise ClaimEvidenceError(
                    f"claim {self.claim_id!r} cannot be {self.status!r} while a blocking "
                    "missing check is declared"
                )
        if self.status == "not_evaluated" and not self.missing_checks:
            raise ClaimEvidenceError(
                f"claim {self.claim_id!r} is not_evaluated but lists no missing check"
            )
        if self.status == "inconclusive" and not self.source_refs and not self.missing_checks:
            raise ClaimEvidenceError(
                f"claim {self.claim_id!r} is inconclusive but names neither partial evidence "
                "nor a missing check"
            )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "claim") -> Claim:
        """Read one claim; unknown fields and unknown statuses are rejected."""
        data = _mapping(payload, where)
        _reject_unknown(data, _CLAIM_KEYS, where)
        _require_keys(
            data,
            ("claim_id", "statement", "status", "scope", "source_refs", "missing_checks"),
            where,
        )
        status = data["status"]
        if not isinstance(status, str) or status not in CLAIM_STATUSES:
            raise UnknownClaimStatusError(
                f"{where}.status is {status!r}; the frozen vocabulary is {sorted(CLAIM_STATUSES)}"
            )
        raw_validator = data.get("validator")
        raw_sources = data.get("source_refs", [])
        raw_missing = data.get("missing_checks", [])
        raw_limitations = data.get("limitations", [])
        return cls(
            claim_id=_string(data["claim_id"], f"{where}.claim_id"),
            statement=_string(data["statement"], f"{where}.statement"),
            status=status,
            scope=ClaimScope.from_dict(data["scope"], f"{where}.scope"),
            source_refs=tuple(
                ArtifactSourceRef.from_dict(item, f"{where}.source_refs[{index}]")
                for index, item in enumerate(_sequence(raw_sources, f"{where}.source_refs"))
            ),
            missing_checks=tuple(
                EvidenceMissingCheck.from_dict(item, f"{where}.missing_checks[{index}]")
                for index, item in enumerate(_sequence(raw_missing, f"{where}.missing_checks"))
            ),
            validator=(
                None
                if raw_validator is None
                else EvidenceValidator.from_dict(raw_validator, f"{where}.validator")
            ),
            limitations=tuple(
                _string(item, f"{where}.limitations[{index}]")
                for index, item in enumerate(_sequence(raw_limitations, f"{where}.limitations"))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped claim object."""
        document: dict[str, Any] = {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "status": self.status,
            "scope": self.scope.to_dict(),
            "source_refs": [item.to_dict() for item in self.source_refs],
            "missing_checks": [item.to_dict() for item in self.missing_checks],
            "limitations": list(self.limitations),
        }
        if self.validator is not None:
            document["validator"] = self.validator.to_dict()
        return document

    def status_row(self) -> dict[str, Any]:
        """Return the compact claim-status table row used by acceptance output."""
        return {
            "claim_id": self.claim_id,
            "status": self.status,
            "validator_id": None if self.validator is None else self.validator.validator_id,
            "validator_version": (
                None if self.validator is None else self.validator.validator_version
            ),
            "validator_kind": None if self.validator is None else self.validator.kind,
            "source_ref_count": len(self.source_refs),
            "source_refs": [item.to_dict() for item in self.source_refs],
            "limitation_ids": list(self.limitations),
            "limitations": list(self.limitations),
            "missing_checks": [item.to_dict() for item in self.missing_checks],
        }


# Alias used by assembly helpers; ``ClaimDraft`` may be built before the
# evidence floor is applied and is not itself a validated fact.
@dataclass(frozen=True, slots=True)
class ClaimDraft:
    """An unvalidated claim candidate supplied to :func:`assemble_claim`."""

    claim_id: str
    statement: str
    status: str
    scope: ClaimScope
    source_refs: tuple[ArtifactSourceRef, ...] = ()
    missing_checks: tuple[EvidenceMissingCheck, ...] = ()
    validator: EvidenceValidator | None = None
    limitations: tuple[str, ...] = ()


# ── execution facts, subject, report ───────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionFacts:
    """Process execution facts; never scientific claim status."""

    terminal_state: str
    argv: tuple[str, ...]
    exit_code: int | None
    started_at: str | None = None
    finished_at: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None

    def __post_init__(self) -> None:
        if self.terminal_state not in TERMINAL_STATES:
            raise EvidenceContractError(
                f"execution.terminal_state must be one of {sorted(TERMINAL_STATES)}, "
                f"got {self.terminal_state!r}"
            )
        object.__setattr__(
            self, "argv", tuple(_string(item, "execution.argv[]") for item in self.argv)
        )
        if self.exit_code is not None:
            object.__setattr__(self, "exit_code", _int(self.exit_code, "execution.exit_code"))
        for name, value in (("started_at", self.started_at), ("finished_at", self.finished_at)):
            if value is not None:
                text = _string(value, f"execution.{name}")
                if _TIMESTAMP_PATTERN.fullmatch(text) is None:
                    raise EvidenceContractError(
                        f"execution.{name} must be an RFC 3339 timestamp, got {text!r}"
                    )
                object.__setattr__(self, name, text)
        for name in ("stdout_path", "stderr_path"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _relative_artifact_path(value, f"execution.{name}"))

    @property
    def completed(self) -> bool:
        """Whether the process reached the ``completed`` terminal state."""
        return self.terminal_state == "completed"

    @classmethod
    def from_dict(cls, payload: Any, where: str = "execution") -> ExecutionFacts:
        """Read execution facts."""
        data = _mapping(payload, where)
        _reject_unknown(data, _EXECUTION_KEYS, where)
        _require_keys(data, ("terminal_state", "argv", "exit_code"), where)
        return cls(
            terminal_state=_string(data["terminal_state"], f"{where}.terminal_state"),
            argv=tuple(
                _string(item, f"{where}.argv[{index}]")
                for index, item in enumerate(_sequence(data["argv"], f"{where}.argv"))
            ),
            exit_code=_optional_int(data["exit_code"], f"{where}.exit_code"),
            started_at=_optional_string(data.get("started_at"), f"{where}.started_at"),
            finished_at=_optional_string(data.get("finished_at"), f"{where}.finished_at"),
            stdout_path=_optional_string(data.get("stdout_path"), f"{where}.stdout_path"),
            stderr_path=_optional_string(data.get("stderr_path"), f"{where}.stderr_path"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped execution facts."""
        return {
            "terminal_state": self.terminal_state,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }


@dataclass(frozen=True, slots=True)
class Subject:
    """The scientific subject an evidence report describes."""

    subject_id: str
    subject_kind: str
    description: str | None = None
    input_digests: tuple[Digest, ...] = ()
    science_identity: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_id", _string(self.subject_id, "subject.subject_id"))
        if self.subject_kind not in SUBJECT_KINDS:
            raise EvidenceContractError(
                f"subject.subject_kind must be one of {sorted(SUBJECT_KINDS)}, "
                f"got {self.subject_kind!r}"
            )
        object.__setattr__(
            self, "description", _optional_string(self.description, "subject.description")
        )
        object.__setattr__(self, "input_digests", tuple(self.input_digests))
        for index, digest in enumerate(self.input_digests):
            if not isinstance(digest, Digest):
                raise EvidenceContractError(f"subject.input_digests[{index}] must be a Digest")
        if self.science_identity is not None:
            identity = _string(self.science_identity, "subject.science_identity")
            if SCIENCE_IDENTITY_PATTERN.fullmatch(identity) is None:
                raise EvidenceContractError("subject.science_identity must match ^v2:[0-9a-f]{32}$")
            object.__setattr__(self, "science_identity", identity)

    @classmethod
    def from_dict(cls, payload: Any, where: str = "subject") -> Subject:
        """Read a report subject."""
        data = _mapping(payload, where)
        _reject_unknown(data, _SUBJECT_KEYS, where)
        _require_keys(data, ("subject_id", "subject_kind"), where)
        raw_digests = data.get("input_digests", [])
        return cls(
            subject_id=_string(data["subject_id"], f"{where}.subject_id"),
            subject_kind=_string(data["subject_kind"], f"{where}.subject_kind"),
            description=_optional_string(data.get("description"), f"{where}.description"),
            input_digests=tuple(
                Digest.from_dict(item, f"{where}.input_digests[{index}]")
                for index, item in enumerate(_sequence(raw_digests, f"{where}.input_digests"))
            ),
            science_identity=_optional_string(
                data.get("science_identity"), f"{where}.science_identity"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped subject."""
        document: dict[str, Any] = {
            "subject_id": self.subject_id,
            "subject_kind": self.subject_kind,
        }
        if self.description is not None:
            document["description"] = self.description
        if self.input_digests:
            document["input_digests"] = [digest.to_dict() for digest in self.input_digests]
        document["science_identity"] = self.science_identity
        return document


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    """A schema_version 1 evidence report with execution/claims separated."""

    report_id: str
    subject: Subject
    execution: ExecutionFacts
    claims: tuple[Claim, ...]
    limitations: tuple[Limitation, ...] = ()
    provenance_digest: Digest | None = None
    schema_version: int = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise EvidenceContractError("report.schema_version must be an integer")
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise UnsupportedEvidenceSchemaVersionError(
                f"unsupported evidence schema_version {self.schema_version!r}; "
                f"only {EVIDENCE_SCHEMA_VERSION} is readable"
            )
        object.__setattr__(self, "report_id", _string(self.report_id, "report.report_id"))
        if not isinstance(self.subject, Subject):
            raise EvidenceContractError("report.subject must be a Subject")
        if not isinstance(self.execution, ExecutionFacts):
            raise EvidenceContractError("report.execution must be ExecutionFacts")
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "limitations", tuple(self.limitations))
        if not self.claims:
            raise EvidenceContractError("report.claims must contain at least one claim")
        seen_claims: set[str] = set()
        for claim in self.claims:
            if not isinstance(claim, Claim):
                raise EvidenceContractError("report.claims entries must be Claim objects")
            if claim.claim_id in seen_claims:
                raise EvidenceContractError(f"duplicate claim_id {claim.claim_id!r}")
            seen_claims.add(claim.claim_id)
        seen_limits: set[str] = set()
        for limitation in self.limitations:
            if not isinstance(limitation, Limitation):
                raise EvidenceContractError("report.limitations entries must be Limitation objects")
            if limitation.limitation_id in seen_limits:
                raise EvidenceContractError(f"duplicate limitation_id {limitation.limitation_id!r}")
            seen_limits.add(limitation.limitation_id)
        for claim in self.claims:
            unknown = sorted(set(claim.limitations) - seen_limits)
            if unknown:
                raise EvidenceContractError(
                    f"claim {claim.claim_id!r} references undeclared limitation(s) {unknown}"
                )
        if self.provenance_digest is not None and not isinstance(self.provenance_digest, Digest):
            raise EvidenceContractError("report.provenance_digest must be a Digest or None")

    @classmethod
    def from_dict(cls, payload: Any, where: str = "report") -> EvidenceReport:
        """Read a closed evidence report; unknown fields are rejected."""
        data = _mapping(payload, where)
        _reject_unknown(data, _REPORT_KEYS, where)
        _require_keys(
            data,
            (
                "schema_version",
                "report_id",
                "subject",
                "execution",
                "claims",
                "limitations",
            ),
            where,
        )
        version = data["schema_version"]
        if isinstance(version, bool) or not isinstance(version, int):
            raise EvidenceContractError(f"{where}.schema_version must be an integer")
        if version != EVIDENCE_SCHEMA_VERSION:
            raise UnsupportedEvidenceSchemaVersionError(
                f"{where}.schema_version is {version!r}; only {EVIDENCE_SCHEMA_VERSION} is readable"
            )
        raw_limitations = data.get("limitations", [])
        raw_provenance = data.get("provenance_digest")
        return cls(
            report_id=_string(data["report_id"], f"{where}.report_id"),
            subject=Subject.from_dict(data["subject"], f"{where}.subject"),
            execution=ExecutionFacts.from_dict(data["execution"], f"{where}.execution"),
            claims=tuple(
                Claim.from_dict(item, f"{where}.claims[{index}]")
                for index, item in enumerate(_sequence(data["claims"], f"{where}.claims"))
            ),
            limitations=tuple(
                Limitation.from_dict(item, f"{where}.limitations[{index}]")
                for index, item in enumerate(_sequence(raw_limitations, f"{where}.limitations"))
            ),
            provenance_digest=(
                None
                if raw_provenance is None
                else Digest.from_dict(raw_provenance, f"{where}.provenance_digest")
            ),
            schema_version=version,
        )

    @property
    def claim_statuses(self) -> dict[str, str]:
        """Map claim id to its frozen status."""
        return {claim.claim_id: claim.status for claim in self.claims}

    @property
    def overall_claim_status(self) -> str:
        """A conservative summary status; never upgrades a constituent claim.

        Precedence is ``refuted`` > ``inconclusive`` > ``not_evaluated`` >
        ``supported`` so one unsupported claim can never be hidden by another
        supported claim.
        """
        statuses = {claim.status for claim in self.claims}
        for status in ("refuted", "inconclusive", "not_evaluated"):
            if status in statuses:
                return status
        return "supported"

    def claim(self, claim_id: str) -> Claim | None:
        """Return the claim with *claim_id*, or ``None``."""
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        return None

    def status_rows(self) -> list[dict[str, Any]]:
        """Return the claim-status table rows."""
        return [claim.status_row() for claim in self.claims]

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped evidence report."""
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "report_id": self.report_id,
            "subject": self.subject.to_dict(),
            "execution": self.execution.to_dict(),
            "claims": [claim.to_dict() for claim in self.claims],
            "limitations": [limitation.to_dict() for limitation in self.limitations],
        }
        if self.provenance_digest is not None:
            document["provenance_digest"] = self.provenance_digest.to_dict()
        return document


# ── assembly (completed steps only) ────────────────────────────────────────


def _downgrade_for_missing_evidence(
    draft: ClaimDraft, execution: ExecutionFacts
) -> tuple[str, tuple[EvidenceMissingCheck, ...]]:
    """Return the honest status/missing checks when the evidence floor is unmet."""
    missing = list(draft.missing_checks)
    status = draft.status
    if status not in ("supported", "refuted"):
        return status, tuple(missing)
    problems: list[str] = []
    if draft.validator is None:
        problems.append("no validator id/version")
        missing.append(
            EvidenceMissingCheck(
                check_id="validator_unavailable",
                reason="the step produced no admissible validator identity",
            )
        )
    if not draft.source_refs:
        problems.append("no digested source reference")
        missing.append(
            EvidenceMissingCheck(
                check_id="artifact_missing",
                reason="the step produced no digested artifact for this claim",
            )
        )
    if any(check.blocking for check in missing):
        problems.append("a blocking missing check is declared")
    if not problems:
        return status, tuple(missing)
    if execution.completed:
        logger.warning(
            "downgrading claim %s from %s to inconclusive: %s",
            draft.claim_id,
            status,
            ", ".join(problems),
        )
        return "inconclusive", tuple(missing)
    logger.warning(
        "downgrading claim %s from %s to not_evaluated: %s",
        draft.claim_id,
        status,
        ", ".join(problems),
    )
    missing.append(
        EvidenceMissingCheck(
            check_id="not_executed",
            reason="the execution did not reach the completed terminal state",
        )
    )
    return "not_evaluated", tuple(missing)


def assemble_claim(draft: ClaimDraft, execution: ExecutionFacts) -> Claim:
    """Build a claim from a completed-step draft with the honest downgrade rule.

    A ``supported``/``refuted`` draft that lacks a validator or a digested
    source becomes ``inconclusive`` when the process completed (evidence was
    insufficient) and ``not_evaluated`` when it did not.  It is never silently
    upgraded, and a violated floor never escapes as a validated claim.
    """
    if not isinstance(draft, ClaimDraft):
        raise EvidenceContractError("assemble_claim requires a ClaimDraft")
    if not isinstance(execution, ExecutionFacts):
        raise EvidenceContractError("assemble_claim requires ExecutionFacts")
    status, missing_checks = _downgrade_for_missing_evidence(draft, execution)
    return Claim(
        claim_id=draft.claim_id,
        statement=draft.statement,
        status=status,
        scope=draft.scope,
        source_refs=draft.source_refs,
        missing_checks=missing_checks,
        validator=draft.validator,
        limitations=draft.limitations,
    )


def claim_draft_from_assessment(
    assessment: Any,
    *,
    scope: ClaimScope,
    source_refs: Sequence[ArtifactSourceRef] = (),
    limitations: Sequence[str] = (),
    validator_kind: str = "deterministic",
    missing_checks: Sequence[EvidenceMissingCheck] = (),
) -> ClaimDraft:
    """Convert a deterministic validator assessment into an unvalidated draft.

    *assessment* is duck-typed as the ``ClaimAssessment`` shape produced by
    :mod:`cccp.analysis.research.validators`; this module does not import the
    validator implementation.  The draft keeps the assessment's status and
    validator identity, but :func:`assemble_claim` still applies the evidence
    floor and downgrades a claim that has no digested source.
    """
    try:
        claim_id = _string(getattr(assessment, "claim_id"), "assessment.claim_id")
        statement = _string(getattr(assessment, "statement"), "assessment.statement")
        status = _string(getattr(assessment, "status"), "assessment.status")
        validator = EvidenceValidator(
            _string(getattr(assessment, "validator_id"), "assessment.validator_id"),
            _string(
                getattr(assessment, "validator_version"),
                "assessment.validator_version",
            ),
            validator_kind,
        )
    except AttributeError as exc:
        raise EvidenceContractError(
            "claim_draft_from_assessment requires an assessment with claim_id, "
            "statement, status, validator_id and validator_version"
        ) from exc
    inherited: list[EvidenceMissingCheck] = list(missing_checks)
    for index, check in enumerate(getattr(assessment, "missing_checks", ())):
        check_id = getattr(check, "check_id", None)
        reason = getattr(check, "reason", None)
        blocking = getattr(check, "blocking", True)
        if isinstance(check_id, str) and isinstance(reason, str):
            inherited.append(
                EvidenceMissingCheck(check_id=check_id, reason=reason, blocking=blocking)
            )
        else:
            raise EvidenceContractError(
                f"assessment.missing_checks[{index}] is not a missing-check record"
            )
    return ClaimDraft(
        claim_id=claim_id,
        statement=statement,
        status=status,
        scope=scope,
        source_refs=tuple(source_refs),
        missing_checks=tuple(inherited),
        validator=validator,
        limitations=tuple(limitations),
    )


def assemble_evidence_report(
    *,
    report_id: str,
    subject: Subject,
    execution: ExecutionFacts,
    drafts: Sequence[ClaimDraft],
    limitations: Sequence[Limitation] = (),
    provenance_digest: Digest | None = None,
) -> EvidenceReport:
    """Assemble the final report from actually completed steps.

    Only drafts supplied by the caller are considered; this function never
    invents a claim, artifact or validator.  Each draft passes through
    :func:`assemble_claim`, so incomplete evidence is downgraded rather than
    asserted.
    """
    claims = tuple(assemble_claim(draft, execution) for draft in drafts)
    return EvidenceReport(
        report_id=report_id,
        subject=subject,
        execution=execution,
        claims=claims,
        limitations=tuple(limitations),
        provenance_digest=provenance_digest,
    )


# ── model narrative (explicitly not evidence) ──────────────────────────────


@dataclass(frozen=True, slots=True)
class ModelNarrative:
    """A model-generated explanation that is *never* validated evidence.

    It exists so a report builder can keep such text out of the claim slots
    without losing it.  Constructing it with ``is_evidence=True`` is rejected.
    """

    text: str
    model_id: str
    is_evidence: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", _string(self.text, "narrative.text"))
        object.__setattr__(self, "model_id", _string(self.model_id, "narrative.model_id"))
        if self.is_evidence:
            raise ModelConclusionError(
                "a model-generated natural-language conclusion is not a validated fact; "
                "it can never be recorded as evidence"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return an explicitly non-evidence view."""
        return {
            "kind": "model_narrative",
            "model_id": self.model_id,
            "text": self.text,
            "is_evidence": False,
            "claim_status": None,
        }


def reject_model_conclusion_as_evidence(value: Any) -> None:
    """Reject a mapping that tries to enter a claim as model-derived evidence."""
    if not isinstance(value, Mapping):
        return
    for key in ("validator", "claim", "evidence"):
        candidate = value.get(key)
        if isinstance(candidate, Mapping):
            reject_model_conclusion_as_evidence(candidate)
    kind = value.get("kind")
    identifier = value.get("validator_id")
    if kind in ("model", "llm", "prompt") or (
        isinstance(identifier, str) and identifier.startswith(("llm:", "model:", "prompt:"))
    ):
        raise ModelConclusionError("model/LLM output cannot be a validator or a validated claim")


# ── source-digest verification ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SourceVerification:
    """One verified source reference."""

    claim_id: str | None
    artifact_path: str
    declared_digest: str
    actual_digest: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe verification row."""
        return {
            "claim_id": self.claim_id,
            "artifact_path": self.artifact_path,
            "declared_digest": self.declared_digest,
            "actual_digest": self.actual_digest,
            "role": self.role,
            "status": "PASS",
        }


def verify_artifact_digest(relative_path: str, digest: Digest, read_root: Path) -> tuple[Path, str]:
    """Resolve *relative_path* below *read_root* and verify its bytes.

    Returns the resolved path and the actual bare sha256.  Absolute paths,
    parent traversal and symlinks that escape the root are rejected before any
    bytes are read.
    """
    if not isinstance(digest, Digest):
        raise EvidenceContractError("verify_artifact_digest requires a Digest")
    path_text = _relative_artifact_path(relative_path, "artifact_path")
    root = Path(read_root).resolve()
    resolved = (root / path_text).resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise EvidencePathError(
            f"artifact path {relative_path!r} resolves outside the evidence root {root}"
        )
    if not resolved.is_file():
        raise EvidenceSourceUnavailableError(
            f"artifact {relative_path!r} is absent below the evidence root {root}"
        )
    try:
        payload = resolved.read_bytes()
    except OSError as exc:
        raise EvidenceSourceUnavailableError(
            f"artifact {relative_path!r} is unreadable: {exc}"
        ) from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != digest.value:
        raise EvidenceDigestMismatchError(
            f"artifact {relative_path!r} digest mismatch: declared {digest.value}, actual {actual}"
        )
    return resolved, actual


def verify_claim_sources(report: EvidenceReport, read_root: Path) -> tuple[SourceVerification, ...]:
    """Verify every claim source below *read_root*; fail on the first problem."""
    if not isinstance(report, EvidenceReport):
        raise EvidenceContractError("verify_claim_sources requires an EvidenceReport")
    rows: list[SourceVerification] = []
    for claim in report.claims:
        for source_ref in claim.source_refs:
            _resolved, actual = verify_artifact_digest(
                source_ref.artifact_path, source_ref.digest, read_root
            )
            rows.append(
                SourceVerification(
                    claim_id=claim.claim_id,
                    artifact_path=source_ref.artifact_path,
                    declared_digest=source_ref.digest.value,
                    actual_digest=actual,
                    role=source_ref.role,
                )
            )
    return tuple(rows)


def canonical_evidence_json(report: EvidenceReport) -> str:
    """Return the frozen canonical JSON encoding of *report*."""
    if not isinstance(report, EvidenceReport):
        raise EvidenceContractError("canonical_evidence_json requires an EvidenceReport")
    return canonical_json(report.to_dict())


def evidence_report_from_dict(payload: Any) -> EvidenceReport:
    """Convenience wrapper around :meth:`EvidenceReport.from_dict`."""
    return EvidenceReport.from_dict(payload)


# ── calculation-contract adapters (one direction, lazy import) ─────────────


def digest_from_checksum(checksum: Any, *, where: str = "artifact.checksum") -> Digest:
    """Return a :class:`Digest` from a bare or ``sha256:``-prefixed checksum.

    The runtime ``cccp.calculation.contracts.ArtifactRef`` stores its checksum
    as free text while the science contract stores a structured bare-lowercase
    sha256.  Both ``<64 hex>`` and ``sha256:<64 hex>`` are accepted; any other
    algorithm, an empty string and a missing checksum are rejected.  A digest
    is never invented: refusing here is the only honest outcome, because a
    source reference without a verified digest cannot support a claim.
    """
    if checksum is None:
        raise EvidenceContractError(f"{where}: no checksum declared; cannot build a digest")
    text = _string(checksum, where).strip()
    if not text:
        raise EvidenceContractError(f"{where}: empty checksum; cannot build a digest")
    prefix, separator, value = text.partition(":")
    if separator:
        if prefix.lower() != "sha256":
            raise EvidenceContractError(
                f"{where}: unsupported digest algorithm {prefix!r}; only sha256 is legal"
            )
    else:
        value = text
    return Digest(value=value.lower())


def _calculation_artifact_relative_path(path: Path, read_root: Path | None) -> str:
    """Return the contract-relative path of a runtime artifact.

    A relative runtime path is kept as declared.  An absolute path is refused
    unless *read_root* is supplied, in which case it must resolve below it.
    """
    raw = Path(path)
    if read_root is None:
        if raw.is_absolute():
            raise EvidencePathError(
                f"artifact.path {str(raw)!r} is absolute; pass read_root to relativise it"
            )
        return _relative_artifact_path(str(raw), "artifact.path")
    root = Path(read_root).resolve()
    try:
        relative = raw.resolve().relative_to(root)
    except ValueError as exc:
        raise EvidencePathError(
            f"artifact.path {str(raw)!r} is not below read_root {str(root)!r}"
        ) from exc
    return _relative_artifact_path(relative.as_posix(), "artifact.path")


def artifact_source_ref_from_calculation_artifact(
    artifact: Any,
    *,
    role: str,
    units: str | None,
    quantity: str | None = None,
    read_root: Path | None = None,
) -> ArtifactSourceRef:
    """Adapt a runtime calculation ``ArtifactRef`` into an :class:`ArtifactSourceRef`.

    This is the **single** direction (calculation → science).  The runtime type
    is imported lazily so importing this module stays independent of
    ``cccp.calculation``; the runtime class is never redefined here.

    ``role`` and ``units`` are required keyword arguments: the runtime record
    knows the artifact *type* but not the scientific role it plays in a claim,
    and the frozen schema requires both fields.  ``units=None`` is the explicit
    statement "not a numeric quantity".  A missing checksum raises rather than
    fabricating one.
    """
    from cccp.calculation.contracts import ArtifactRef as CalculationArtifactRef

    if not isinstance(artifact, CalculationArtifactRef):
        raise EvidenceContractError(
            "artifact_source_ref_from_calculation_artifact requires a "
            f"cccp.calculation.contracts.ArtifactRef, got {type(artifact).__name__}"
        )
    return ArtifactSourceRef(
        artifact_path=_calculation_artifact_relative_path(artifact.path, read_root),
        digest=digest_from_checksum(artifact.checksum),
        role=role,
        units=units,
        quantity=quantity,
    )


# ── historical payload adapter ─────────────────────────────────────────────

#: Declared field tree of the v1 report, used only to *report* undeclared
#: fields in a historical payload.  It is derived from the same key sets the
#: strict reader enforces, so an unknown path here always means "the strict
#: reader would reject it", never "the schema allows it".
_EVIDENCE_FIELD_TREE: Final[dict[str, Any]] = {
    "keys": _REPORT_KEYS,
    "children": {
        "subject": {"keys": _SUBJECT_KEYS},
        "execution": {"keys": _EXECUTION_KEYS},
        "claims": {
            "keys": _CLAIM_KEYS,
            "list": True,
            "children": {
                "scope": {"keys": _SCOPE_KEYS},
                "source_refs": {"keys": _SOURCE_REF_KEYS, "list": True},
                "missing_checks": {"keys": _MISSING_CHECK_KEYS, "list": True},
                "validator": {"keys": _VALIDATOR_KEYS},
            },
        },
        "limitations": {"keys": _LIMITATION_KEYS, "list": True},
        "provenance_digest": {"keys": DIGEST_KEYS},
    },
}


def _collect_unknown_evidence_paths(value: Any, spec: Mapping[str, Any], path: str) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    declared: frozenset[str] = spec["keys"]
    children: Mapping[str, Mapping[str, Any]] = spec.get("children", {})
    found: list[str] = []
    for key, child_value in value.items():
        child_path = f"{path}.{key}"
        if key not in declared:
            found.append(child_path)
            continue
        child_spec = children.get(key)
        if child_spec is None:
            continue
        if child_spec.get("list"):
            if not isinstance(child_value, (list, tuple)):
                continue
            for index, item in enumerate(child_value):
                found.extend(
                    _collect_unknown_evidence_paths(item, child_spec, f"{child_path}[{index}]")
                )
        else:
            found.extend(_collect_unknown_evidence_paths(child_value, child_spec, child_path))
    return found


def unknown_evidence_field_paths(payload: Any) -> tuple[str, ...]:
    """Return the sorted JSON paths a v1 reader would reject, without coercion."""
    if not isinstance(payload, Mapping):
        return ()
    return tuple(sorted(_collect_unknown_evidence_paths(payload, _EVIDENCE_FIELD_TREE, "$")))


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceRecord:
    """A readable historical evidence payload with no execution eligibility.

    ``payload`` is a deep copy of the declared document: nothing is normalized,
    defaulted or dropped.  ``schema_version`` is ``None`` when the payload does
    not declare an integer version — a missing version is never filled with the
    current one.  Tolerating an older payload never grants execution or
    recovery eligibility; those require the strict v1 reader.
    """

    payload: dict[str, Any]
    schema_version: int | None
    adapter: str = "historical_evidence_v1"
    execution_eligible: bool = False
    recovery_eligible: bool = False

    @property
    def historical(self) -> bool:
        """Whether the record predates the current evidence schema."""
        return self.schema_version != EVIDENCE_SCHEMA_VERSION

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        """Undeclared field paths retained verbatim in :attr:`payload`."""
        return unknown_evidence_field_paths(self.payload)

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the preserved payload (byte-stable re-read)."""
        return copy.deepcopy(self.payload)


def read_historical_evidence(
    payload: Any, *, schema_version: int | None = None
) -> HistoricalEvidenceRecord:
    """Read an older evidence payload without canonical coercion.

    No field is defaulted and no unknown field is removed.  A payload that is
    missing a required field stays missing; the caller must not read the
    absence as an implicit value.  Use :meth:`EvidenceReport.from_dict` for a
    v1 object eligible for new work.
    """
    data = _mapping(payload, "historical_evidence")
    declared = schema_version
    if declared is None:
        raw = data.get("schema_version")
        if isinstance(raw, int) and not isinstance(raw, bool):
            declared = raw
    return HistoricalEvidenceRecord(
        payload=copy.deepcopy(data),
        schema_version=declared,
        execution_eligible=False,
        recovery_eligible=False,
    )
