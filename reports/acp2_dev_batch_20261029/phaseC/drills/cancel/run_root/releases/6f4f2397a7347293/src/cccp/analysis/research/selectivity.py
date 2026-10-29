"""Bounded selectivity evidence consumer for ACP2 (A-25b).

This module consumes the A-25a frozen selectivity fixture and the artifacts that
the platform owner (B23b) exports after C T-40a's single research submission.
It does **not** submit a job, run a QC executable, or create a second evidence
schema: every claim is assembled through the frozen
:mod:`cccp.analysis.research.evidence` contract and every source is a digested
relative artifact below the caller's ``read_root``.

The consumer is deliberately conservative:

* both competing paths must be qualified before any relative preference is
  stated; one missing or unsupported path makes the preference
  ``inconclusive`` and never selects the other path by default;
* the reference must be the registered
  ``owner=C, scenario=experience:cross-domain-live, subcase=selectivity``
  record with a unique public ``(owner, scenario, phase, run_id)`` key and the
  registered ``experience:selectivity`` phase;
* the B23b edge must be ``integration_ready`` (artifacts exported), never
  inferred from a B23 whole-task status or from a numeric ``23`` prefix;
* the cross-domain budget must record the ``nmr``, ``thermo`` and
  ``selectivity`` subrows with per-subrow TaskRef/step/QC counts, the
  cumulative total and reused artifacts; a changed ``run_id`` never resets the
  cumulative consumption;
* absent reference/B23b/budget/artifacts leave the platform state
  ``NOT_VERIFIED`` and H6 open; a constructed demonstration is never a real
  run.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from cccp.analysis.research.evidence import (
    ArtifactSourceRef,
    ClaimDraft,
    ClaimScope,
    Digest,
    EvidenceContractError,
    EvidenceMissingCheck,
    EvidenceReport,
    EvidenceValidator,
    ExecutionFacts,
    Limitation,
    Subject,
    assemble_evidence_report,
    evidence_report_from_dict,
    verify_artifact_digest,
    verify_claim_sources,
)
from cccp.analysis.research.validators import (
    ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID,
    OPTIMIZATION_CONVERGENCE_VALIDATOR_ID,
    TS_CONNECTIVITY_VALIDATOR_ID,
    TS_SADDLE_CANDIDATE_VALIDATOR_ID,
)

logger = logging.getLogger(__name__)

__all__ = [
    "B23B_EDGE_ARTIFACT_ID",
    "B23B_READINESS",
    "BUDGET_DOMAINS",
    "PREFERENCE_TOLERANCE_KCAL_PER_MOL",
    "SELECTIVITY_OWNER",
    "SELECTIVITY_PHASE",
    "SELECTIVITY_SCENARIO",
    "SELECTIVITY_SUBCASE",
    "SELECTIVITY_VALIDATOR_VERSION",
    "B23bArtifacts",
    "CrossDomainBudgetTable",
    "SelectivityAuditResult",
    "SelectivityAuthorizedArtifact",
    "SelectivityBudgetSubrow",
    "SelectivityPathEvaluation",
    "SelectivityPathRecord",
    "SelectivityPublicKey",
    "SelectivityReferenceError",
    "build_selectivity_audit",
    "cross_domain_budget_validator_id",
    "selectivity_reference_validator_id",
    "selectivity_relative_preference_validator_id",
]

#: Registered reference values.  ``subcase`` is display metadata only.
SELECTIVITY_OWNER: Final = "C"
SELECTIVITY_SCENARIO: Final = "experience:cross-domain-live"
SELECTIVITY_SUBCASE: Final = "selectivity"
SELECTIVITY_PHASE: Final = "experience:selectivity"

#: B23b's delivered-state readiness for the exported artifact set.  This is
#: deliberately not the B23 whole-task ``verified`` state.
B23B_READINESS: Final = "integration_ready"

#: A's declared edge id for the B23b selectivity artifact set
#: (``cross-track-dependencies.json``).  It is an *edge* id used to name the
#: missing producer honestly; it is **not** the producer's machine id.  The
#: machine id is published by B23b and is only recorded verbatim when supplied.
B23B_EDGE_ARTIFACT_ID: Final = "B23b:selectivity-authorized-artifacts"

#: Cross-domain budget subrows the selectivity audit must consume.
BUDGET_DOMAINS: Final[tuple[str, str, str]] = ("nmr", "thermo", "selectivity")

#: Relative activation-energy preference threshold from the A-25a manifest.
PREFERENCE_TOLERANCE_KCAL_PER_MOL: Final = 0.5

SELECTIVITY_VALIDATOR_VERSION: Final = "1.0.0"

#: Deterministic claims a qualified path report must carry.
REQUIRED_PATH_CLAIM_IDS: Final[dict[str, str]] = {
    "c-optimization-converged": OPTIMIZATION_CONVERGENCE_VALIDATOR_ID,
    "c-ts-first-order-saddle-candidate": TS_SADDLE_CANDIDATE_VALIDATOR_ID,
    "c-ts-connects-intended-endpoints": TS_CONNECTIVITY_VALIDATOR_ID,
    "c-electronic-activation-barrier-comparable": (ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID),
}
FREE_ENERGY_CLAIM_ID: Final = "c-free-energy-activation-barrier-comparable"
PATH_IDS: Final[tuple[str, str]] = ("path.endo", "path.exo")

PLATFORM_EVIDENCE_STATES: Final[frozenset[str]] = frozenset({"PASS", "FAIL", "NOT_VERIFIED"})

_SELECTIVITY_REFERENCE_VALIDATOR_ID: Final = "check.selectivity_reference"
_SELECTIVITY_PATH_VALIDATOR_ID: Final = "check.selectivity_path_qualified"
_SELECTIVITY_PREFERENCE_VALIDATOR_ID: Final = "check.selectivity_relative_preference"
_CROSS_DOMAIN_BUDGET_VALIDATOR_ID: Final = "check.cross_domain_budget"

_LIMITATION_IDS: Final[dict[str, str]] = {
    "lim-constructed": (
        "The report is a constructed demonstration; no platform job, QC process "
        "or experimental measurement exists."
    ),
    "lim-platform-not-verified": (
        "The registered reference, B23b integration_ready artifact set or "
        "cross-domain budget table is absent or invalid; the real row is "
        "NOT_VERIFIED."
    ),
    "lim-single-regioisomer": (
        "Only the declared 2-substituted regioisomer is in scope; other "
        "regioisomers are not enumerated."
    ),
    "lim-single-conformer": (
        "One frozen s-cis methyl acrylate conformer and one conformation per "
        "endpoint are in scope; no conformer search or MD sampling was used."
    ),
    "lim-no-experimental": (
        "No experimental quantitative claim is made; the preference is a "
        "relative computational result under the frozen conditions."
    ),
    "lim-budget-unmeasured": (
        "The A-25a budget timing basis is unmeasured; the B/C cross-domain "
        "budget table is authoritative and the cumulative total is never reset "
        "by changing run_id."
    ),
    "lim-h6-open": (
        "H6 remains open until C T-40b signs the presentation after a real "
        "A-25b audit with real evidence."
    ),
    "lim-relative-only": (
        "The stated preference is relative to the declared exo path under the "
        "frozen method, state and conditions; it is not an experimental or "
        "global selectivity prediction."
    ),
}


class SelectivityReferenceError(ValueError):
    """A reference/B23b/budget/path record is structurally invalid."""


# ── small helpers ──────────────────────────────────────────────────────────


def selectivity_reference_validator_id() -> str:
    """Return the validator id used by the reference claim."""
    return _SELECTIVITY_REFERENCE_VALIDATOR_ID


def cross_domain_budget_validator_id() -> str:
    """Return the validator id used by the budget claim."""
    return _CROSS_DOMAIN_BUDGET_VALIDATOR_ID


def selectivity_relative_preference_validator_id() -> str:
    """Return the validator id used by the relative-preference claim."""
    return _SELECTIVITY_PREFERENCE_VALIDATOR_ID


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SelectivityReferenceError(f"{where} must be a JSON object")
    return dict(value)


def _as_sequence(value: Any, where: str) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise SelectivityReferenceError(f"{where} must be a JSON array")
    return list(value)


def _as_string(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SelectivityReferenceError(f"{where} must be a string")
    text = value.strip()
    if not text and not allow_empty:
        raise SelectivityReferenceError(f"{where} must not be empty")
    return text


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SelectivityReferenceError(f"{where} must be an integer")
    if value < 0:
        raise SelectivityReferenceError(f"{where} must be >= 0")
    return value


def _as_finite_float(value: Any, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SelectivityReferenceError(f"{where} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise SelectivityReferenceError(f"{where} must be finite")
    return result


def _digest(value: Any, where: str) -> Digest:
    if isinstance(value, Digest):
        return value
    try:
        return Digest(value=_as_string(value, where))
    except EvidenceContractError as exc:
        raise SelectivityReferenceError(f"{where}: {exc}") from exc


def _relative_path(value: Any, where: str) -> str:
    text = _as_string(value, where)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        raise SelectivityReferenceError(f"{where} must be a relative path without '..'")
    return text


def _missing(check_id: str, reason: str, *, blocking: bool = True) -> EvidenceMissingCheck:
    return EvidenceMissingCheck(check_id=check_id, reason=reason, blocking=blocking)


def _source(
    path: str,
    digest: Digest,
    role: str,
    *,
    units: str | None = None,
    quantity: str | None = None,
) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        artifact_path=path,
        digest=digest,
        role=role,
        units=units,
        quantity=quantity,
    )


def _validator(validator_id: str) -> EvidenceValidator:
    return EvidenceValidator(
        validator_id=validator_id,
        validator_version=SELECTIVITY_VALIDATOR_VERSION,
        kind="deterministic",
    )


def _limits(*ids: str, constructed: bool) -> tuple[str, ...]:
    """Return claim limitation ids, adding the constructed marker when applicable."""
    values = list(ids)
    if constructed:
        values.append("lim-constructed")
    return tuple(values)


def _categorical_signature(signature: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable, geometry-order-independent part of an endpoint signature."""
    return {
        "stereocenter_atom_id": signature.get("stereocenter_atom_id"),
        "cip_configuration": signature.get("cip_configuration"),
        "signed_volume_sign": signature.get("signed_volume_sign"),
        "face": signature.get("face"),
    }


# ── public key and records ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SelectivityPublicKey:
    """The public reference key; ``subcase`` is display metadata only."""

    owner: str
    scenario: str
    phase: str
    run_id: str
    subcase: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner", _as_string(self.owner, "public_key.owner"))
        object.__setattr__(self, "scenario", _as_string(self.scenario, "public_key.scenario"))
        object.__setattr__(self, "phase", _as_string(self.phase, "public_key.phase"))
        object.__setattr__(self, "run_id", _as_string(self.run_id, "public_key.run_id"))
        if self.subcase is not None:
            object.__setattr__(self, "subcase", _as_string(self.subcase, "public_key.subcase"))

    @classmethod
    def from_dict(cls, payload: Any, where: str = "public_key") -> SelectivityPublicKey:
        data = _as_mapping(payload, where)
        return cls(
            owner=_as_string(data.get("owner"), f"{where}.owner"),
            scenario=_as_string(data.get("scenario"), f"{where}.scenario"),
            phase=_as_string(data.get("phase"), f"{where}.phase"),
            run_id=_as_string(data.get("run_id"), f"{where}.run_id"),
            subcase=(
                None
                if data.get("subcase") is None
                else _as_string(data.get("subcase"), f"{where}.subcase")
            ),
        )

    def key(self) -> tuple[str, str, str, str]:
        """Return the public identity tuple, excluding the display-only subcase."""
        return (self.owner, self.scenario, self.phase, self.run_id)

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe public key."""
        return {
            "owner": self.owner,
            "scenario": self.scenario,
            "phase": self.phase,
            "run_id": self.run_id,
            "subcase": self.subcase,
        }


@dataclass(frozen=True, slots=True)
class SelectivityAuthorizedArtifact:
    """One B23b-authorized artifact for a declared path."""

    path_id: str
    report_path: str
    digest: Digest
    role: str
    reused: bool = False

    def __post_init__(self) -> None:
        if self.path_id not in PATH_IDS:
            raise SelectivityReferenceError(
                f"authorized artifact path_id {self.path_id!r} is invalid"
            )
        object.__setattr__(
            self, "report_path", _relative_path(self.report_path, "authorized_artifact.report_path")
        )
        if not isinstance(self.digest, Digest):
            raise SelectivityReferenceError("authorized_artifact.digest must be a Digest")
        object.__setattr__(self, "role", _as_string(self.role, "authorized_artifact.role"))

    @classmethod
    def from_dict(cls, payload: Any, where: str) -> SelectivityAuthorizedArtifact:
        data = _as_mapping(payload, where)
        return cls(
            path_id=_as_string(data.get("path_id"), f"{where}.path_id"),
            report_path=_relative_path(data.get("report_path"), f"{where}.report_path"),
            digest=_digest(data.get("digest"), f"{where}.digest"),
            role=_as_string(data.get("role"), f"{where}.role"),
            reused=bool(data.get("reused", False)),
        )


@dataclass(frozen=True, slots=True)
class B23bArtifacts:
    """The B23b exported artifact set at ``integration_ready``."""

    artifact_id: str
    readiness: str
    run_id: str
    paths: tuple[str, ...]
    authorized_artifacts: tuple[SelectivityAuthorizedArtifact, ...]
    record_path: str
    record_digest: Digest
    real_qc_binary_ran: bool = False
    constructed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_id", _as_string(self.artifact_id, "b23b.artifact_id"))
        object.__setattr__(self, "readiness", _as_string(self.readiness, "b23b.readiness"))
        object.__setattr__(self, "run_id", _as_string(self.run_id, "b23b.run_id"))
        paths = tuple(_as_string(path, "b23b.paths[]") for path in self.paths)
        object.__setattr__(self, "paths", paths)
        object.__setattr__(self, "authorized_artifacts", tuple(self.authorized_artifacts))
        object.__setattr__(
            self, "record_path", _relative_path(self.record_path, "b23b.record_path")
        )
        if not isinstance(self.record_digest, Digest):
            raise SelectivityReferenceError("b23b.record_digest must be a Digest")
        object.__setattr__(self, "constructed", bool(self.constructed))

    @classmethod
    def from_dict(cls, payload: Any, where: str = "b23b") -> B23bArtifacts:
        data = _as_mapping(payload, where)
        return cls(
            artifact_id=_as_string(data.get("artifact_id"), f"{where}.artifact_id"),
            readiness=_as_string(data.get("readiness"), f"{where}.readiness"),
            run_id=_as_string(data.get("run_id"), f"{where}.run_id"),
            paths=tuple(
                _as_string(path, f"{where}.paths[]")
                for path in _as_sequence(data.get("paths"), f"{where}.paths")
            ),
            authorized_artifacts=tuple(
                SelectivityAuthorizedArtifact.from_dict(item, f"{where}.authorized_artifacts[{i}]")
                for i, item in enumerate(
                    _as_sequence(data.get("authorized_artifacts"), f"{where}.authorized_artifacts")
                )
            ),
            record_path=_relative_path(data.get("record_path"), f"{where}.record_path"),
            record_digest=_digest(data.get("record_digest"), f"{where}.record_digest"),
            real_qc_binary_ran=bool(data.get("real_qc_binary_ran", False)),
            constructed=bool(data.get("constructed", False)),
        )

    def authorized_for(self, path_id: str) -> SelectivityAuthorizedArtifact | None:
        """Return the authorized report for *path_id*, if present."""
        matches = [item for item in self.authorized_artifacts if item.path_id == path_id]
        return matches[0] if matches else None


@dataclass(frozen=True, slots=True)
class SelectivityBudgetSubrow:
    """One cross-domain budget subrow (nmr, thermo or selectivity)."""

    domain: str
    run_id: str
    task_ref: str
    task_ref_count: int
    step_count: int
    qc_count: int
    reused: bool
    reused_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.domain not in BUDGET_DOMAINS:
            raise SelectivityReferenceError(f"budget subrow domain {self.domain!r} is invalid")
        object.__setattr__(self, "run_id", _as_string(self.run_id, "budget_subrow.run_id"))
        object.__setattr__(self, "task_ref", _as_string(self.task_ref, "budget_subrow.task_ref"))
        for name in ("task_ref_count", "step_count", "qc_count"):
            object.__setattr__(self, name, _as_int(getattr(self, name), f"budget_subrow.{name}"))
        object.__setattr__(self, "reused_artifacts", tuple(self.reused_artifacts))

    @classmethod
    def from_dict(cls, payload: Any, where: str) -> SelectivityBudgetSubrow:
        data = _as_mapping(payload, where)
        return cls(
            domain=_as_string(data.get("domain"), f"{where}.domain"),
            run_id=_as_string(data.get("run_id"), f"{where}.run_id"),
            task_ref=_as_string(data.get("task_ref"), f"{where}.task_ref"),
            task_ref_count=_as_int(data.get("task_ref_count"), f"{where}.task_ref_count"),
            step_count=_as_int(data.get("step_count"), f"{where}.step_count"),
            qc_count=_as_int(data.get("qc_count"), f"{where}.qc_count"),
            reused=bool(data.get("reused", False)),
            reused_artifacts=tuple(
                _as_string(item, f"{where}.reused_artifacts[]")
                for item in _as_sequence(
                    data.get("reused_artifacts", []), f"{where}.reused_artifacts"
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe subrow."""
        return {
            "domain": self.domain,
            "run_id": self.run_id,
            "task_ref": self.task_ref,
            "task_ref_count": self.task_ref_count,
            "step_count": self.step_count,
            "qc_count": self.qc_count,
            "reused": self.reused,
            "reused_artifacts": list(self.reused_artifacts),
        }


@dataclass(frozen=True, slots=True)
class CrossDomainBudgetTable:
    """The B/C cross-domain budget table consumed by A-25b."""

    run_id: str
    subrows: tuple[SelectivityBudgetSubrow, ...]
    cumulative_task_refs: int
    cumulative_steps: int
    cumulative_qc_calls: int
    cumulative_wall_seconds: float | None
    record_path: str
    record_digest: Digest
    previous_cumulative: Mapping[str, int] | None = None
    previous_run_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _as_string(self.run_id, "budget.run_id"))
        object.__setattr__(self, "subrows", tuple(self.subrows))
        for name in (
            "cumulative_task_refs",
            "cumulative_steps",
            "cumulative_qc_calls",
        ):
            object.__setattr__(self, name, _as_int(getattr(self, name), f"budget.{name}"))
        if self.cumulative_wall_seconds is not None:
            object.__setattr__(
                self,
                "cumulative_wall_seconds",
                _as_finite_float(self.cumulative_wall_seconds, "budget.cumulative_wall_seconds"),
            )
        object.__setattr__(
            self, "record_path", _relative_path(self.record_path, "budget.record_path")
        )
        if not isinstance(self.record_digest, Digest):
            raise SelectivityReferenceError("budget.record_digest must be a Digest")
        object.__setattr__(
            self,
            "previous_run_ids",
            tuple(_as_string(item, "budget.previous_run_ids[]") for item in self.previous_run_ids),
        )
        if self.previous_cumulative is not None:
            previous = _as_mapping(self.previous_cumulative, "budget.previous_cumulative")
            object.__setattr__(self, "previous_cumulative", previous)

    @classmethod
    def from_dict(cls, payload: Any, where: str = "budget") -> CrossDomainBudgetTable:
        data = _as_mapping(payload, where)
        raw_cumulative = _as_mapping(data.get("cumulative"), f"{where}.cumulative")
        raw_previous = data.get("previous_cumulative")
        return cls(
            run_id=_as_string(data.get("run_id"), f"{where}.run_id"),
            subrows=tuple(
                SelectivityBudgetSubrow.from_dict(item, f"{where}.subrows[{index}]")
                for index, item in enumerate(_as_sequence(data.get("subrows"), f"{where}.subrows"))
            ),
            cumulative_task_refs=_as_int(
                raw_cumulative.get("task_refs"), f"{where}.cumulative.task_refs"
            ),
            cumulative_steps=_as_int(raw_cumulative.get("steps"), f"{where}.cumulative.steps"),
            cumulative_qc_calls=_as_int(
                raw_cumulative.get("qc_calls"), f"{where}.cumulative.qc_calls"
            ),
            cumulative_wall_seconds=(
                None
                if raw_cumulative.get("wall_seconds") is None
                else _as_finite_float(
                    raw_cumulative.get("wall_seconds"),
                    f"{where}.cumulative.wall_seconds",
                )
            ),
            record_path=_relative_path(data.get("record_path"), f"{where}.record_path"),
            record_digest=_digest(data.get("record_digest"), f"{where}.record_digest"),
            previous_cumulative=(
                None
                if raw_previous is None
                else _as_mapping(raw_previous, f"{where}.previous_cumulative")
            ),
            previous_run_ids=tuple(
                _as_string(item, f"{where}.previous_run_ids[]")
                for item in _as_sequence(
                    data.get("previous_run_ids", []), f"{where}.previous_run_ids"
                )
            ),
        )

    def subrow(self, domain: str) -> SelectivityBudgetSubrow | None:
        """Return the subrow for *domain*, if present."""
        for row in self.subrows:
            if row.domain == domain:
                return row
        return None

    def totals(self) -> dict[str, int]:
        """Return the summed subrow counts."""
        return {
            "task_refs": sum(row.task_ref_count for row in self.subrows),
            "steps": sum(row.step_count for row in self.subrows),
            "qc_calls": sum(row.qc_count for row in self.subrows),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe budget table."""
        return {
            "run_id": self.run_id,
            "subrows": [row.to_dict() for row in self.subrows],
            "cumulative": {
                "task_refs": self.cumulative_task_refs,
                "steps": self.cumulative_steps,
                "qc_calls": self.cumulative_qc_calls,
                "wall_seconds": self.cumulative_wall_seconds,
            },
            "previous_run_ids": list(self.previous_run_ids),
            "previous_cumulative": dict(self.previous_cumulative)
            if self.previous_cumulative is not None
            else None,
            "record_path": self.record_path,
            "record_digest": self.record_digest.value,
        }


@dataclass(frozen=True, slots=True)
class SelectivityPathRecord:
    """One declared path plus its platform path-evidence report."""

    path_id: str
    report_path: str
    report_digest: Digest
    endpoint_signature: Mapping[str, Any]
    mapping_topology_digest: str
    product_digest: str
    comparison_conditions: Mapping[str, Any]
    activation_energy_kcal_per_mol: float
    free_energy_kcal_per_mol: float | None = None

    def __post_init__(self) -> None:
        if self.path_id not in PATH_IDS:
            raise SelectivityReferenceError(f"path_id {self.path_id!r} is invalid")
        object.__setattr__(
            self, "report_path", _relative_path(self.report_path, "path.report_path")
        )
        if not isinstance(self.report_digest, Digest):
            raise SelectivityReferenceError("path.report_digest must be a Digest")
        object.__setattr__(
            self,
            "endpoint_signature",
            _as_mapping(self.endpoint_signature, "path.endpoint_signature"),
        )
        object.__setattr__(
            self,
            "mapping_topology_digest",
            _as_string(self.mapping_topology_digest, "path.mapping_topology_digest"),
        )
        object.__setattr__(
            self, "product_digest", _as_string(self.product_digest, "path.product_digest")
        )
        object.__setattr__(
            self,
            "comparison_conditions",
            _as_mapping(self.comparison_conditions, "path.comparison_conditions"),
        )
        object.__setattr__(
            self,
            "activation_energy_kcal_per_mol",
            _as_finite_float(
                self.activation_energy_kcal_per_mol,
                "path.activation_energy_kcal_per_mol",
            ),
        )
        if self.free_energy_kcal_per_mol is not None:
            object.__setattr__(
                self,
                "free_energy_kcal_per_mol",
                _as_finite_float(
                    self.free_energy_kcal_per_mol,
                    "path.free_energy_kcal_per_mol",
                ),
            )

    @classmethod
    def from_dict(cls, payload: Any, where: str = "path") -> SelectivityPathRecord:
        data = _as_mapping(payload, where)
        return cls(
            path_id=_as_string(data.get("path_id"), f"{where}.path_id"),
            report_path=_relative_path(data.get("report_path"), f"{where}.report_path"),
            report_digest=_digest(data.get("report_digest"), f"{where}.report_digest"),
            endpoint_signature=_as_mapping(
                data.get("endpoint_signature"), f"{where}.endpoint_signature"
            ),
            mapping_topology_digest=_as_string(
                data.get("mapping_topology_digest"), f"{where}.mapping_topology_digest"
            ),
            product_digest=_as_string(data.get("product_digest"), f"{where}.product_digest"),
            comparison_conditions=_as_mapping(
                data.get("comparison_conditions"), f"{where}.comparison_conditions"
            ),
            activation_energy_kcal_per_mol=_as_finite_float(
                data.get("activation_energy_kcal_per_mol"),
                f"{where}.activation_energy_kcal_per_mol",
            ),
            free_energy_kcal_per_mol=(
                None
                if data.get("free_energy_kcal_per_mol") is None
                else _as_finite_float(
                    data.get("free_energy_kcal_per_mol"),
                    f"{where}.free_energy_kcal_per_mol",
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe path record."""
        return {
            "path_id": self.path_id,
            "report_path": self.report_path,
            "report_digest": self.report_digest.value,
            "endpoint_signature": dict(self.endpoint_signature),
            "mapping_topology_digest": self.mapping_topology_digest,
            "product_digest": self.product_digest,
            "comparison_conditions": dict(self.comparison_conditions),
            "activation_energy_kcal_per_mol": self.activation_energy_kcal_per_mol,
            "free_energy_kcal_per_mol": self.free_energy_kcal_per_mol,
        }


@dataclass(frozen=True, slots=True)
class SelectivityPathEvaluation:
    """The validation result for one declared path."""

    path_id: str
    qualified: bool
    status: str
    gaps: tuple[str, ...]
    claim_statuses: Mapping[str, str]
    report: EvidenceReport | None = None
    report_source: ArtifactSourceRef | None = None


# ── validation helpers ─────────────────────────────────────────────────────


def _expected_comparison(fixture_manifest: Mapping[str, Any]) -> dict[str, Any]:
    comparison = _as_mapping(fixture_manifest.get("comparison"), "fixture.comparison")
    return {
        "method": comparison.get("method"),
        "basis": comparison.get("basis"),
        "charge": comparison.get("charge"),
        "multiplicity": comparison.get("multiplicity"),
        "temperature_k": comparison.get("temperature_k"),
        "pressure_pa": comparison.get("pressure_pa"),
        "standard_state": comparison.get("standard_state"),
        "reference_state": comparison.get("reference_state"),
        "energy_unit": comparison.get("comparison_energy_unit"),
    }


def _budget_requirement(fixture_manifest: Mapping[str, Any]) -> dict[str, Any]:
    raw = fixture_manifest.get("budget")
    if not isinstance(raw, Mapping):
        return {}
    return {
        "source": (
            "A-25a fixture-manifest budget envelope (constructed proposal; the B/C "
            "cross-domain budget table is authoritative)"
        ),
        "max_task_refs": raw.get("max_task_refs"),
        "max_candidate_paths": raw.get("max_candidate_paths"),
        "max_ts_candidates_per_path": raw.get("max_ts_candidates_per_path"),
        "max_conformers_per_endpoint": raw.get("max_conformers_per_endpoint"),
        "max_regioisomers": raw.get("max_regioisomers"),
        "max_qc_invocations": raw.get("max_qc_invocations"),
        "science_deadline_seconds": raw.get("science_deadline_seconds"),
        "export_budget_seconds": raw.get("export_budget_seconds"),
        "aggregate_budget_seconds": raw.get("aggregate_budget_seconds"),
        "budget_timing_basis": raw.get("budget_timing_basis"),
        "required_subrow_domains": list(BUDGET_DOMAINS),
        "constructed": True,
    }


def _fixture_paths(fixture_manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw_paths = _as_sequence(fixture_manifest.get("paths"), "fixture.paths")
    result: dict[str, Mapping[str, Any]] = {}
    for item in raw_paths:
        path = _as_mapping(item, "fixture.paths[]")
        path_id = _as_string(path.get("path_id"), "fixture.paths[].path_id")
        result[path_id] = path
    if set(result) != set(PATH_IDS):
        raise SelectivityReferenceError("fixture must declare exactly path.endo and path.exo")
    return result


def _validate_reference(
    reference_records: Sequence[Mapping[str, Any]],
) -> tuple[SelectivityPublicKey | None, list[str]]:
    gaps: list[str] = []
    parsed: list[SelectivityPublicKey] = []
    for index, raw in enumerate(reference_records):
        try:
            parsed.append(SelectivityPublicKey.from_dict(raw, f"reference_records[{index}]"))
        except (SelectivityReferenceError, ValueError) as exc:
            gaps.append(f"reference_records[{index}]: {exc}")
    selectivity_records = [
        record
        for record in parsed
        if record.owner == SELECTIVITY_OWNER
        and record.scenario == SELECTIVITY_SCENARIO
        and record.subcase == SELECTIVITY_SUBCASE
    ]
    if not selectivity_records:
        gaps.append(
            "selectivity.public_key.missing: no registered reference "
            "owner=C, scenario=experience:cross-domain-live, subcase=selectivity, "
            "phase=experience:selectivity was supplied; the public "
            "(owner, scenario, phase, run_id) key is incomplete because the run_id "
            "is missing (B/C-issued, never invented)"
        )
        return None, gaps
    if len(selectivity_records) > 1:
        gaps.append(
            "selectivity.public_key.duplicate: more than one selectivity reference "
            "record was supplied"
        )
    public_key = selectivity_records[0]
    if public_key.phase != SELECTIVITY_PHASE:
        gaps.append(
            "selectivity.public_key.phase: reference phase "
            f"{public_key.phase!r} is not the registered {SELECTIVITY_PHASE!r}"
        )
    selectivity_key = public_key.key()
    sharing = [
        record
        for record in parsed
        if record.key() == selectivity_key and record.subcase != SELECTIVITY_SUBCASE
    ]
    if sharing:
        subcases = sorted({str(record.subcase) for record in sharing})
        gaps.append(
            "selectivity.public_key.not_unique: two subcases share one public "
            "(owner, scenario, phase, run_id) key: "
            f"selectivity and {subcases}; the result is NOT_VERIFIED"
        )
    return public_key, gaps


def _validate_budget(
    budget: CrossDomainBudgetTable | None,
    public_key: SelectivityPublicKey | None,
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    failures: list[str] = []
    if budget is None:
        return ["selectivity.budget.absent: the B/C cross-domain budget table is absent"], failures
    if public_key is not None and budget.run_id != public_key.run_id:
        gaps.append(
            "selectivity.budget.run_id: the budget table run_id differs from the "
            "public run_id; the cumulative budget must not be reset by changing run_id"
        )
    domains = [row.domain for row in budget.subrows]
    if sorted(domains) != sorted(BUDGET_DOMAINS):
        gaps.append(
            "selectivity.budget.subrows: the budget table must contain exactly the "
            "nmr, thermo and selectivity "
            f"subrows, got {domains}"
        )
    for row in budget.subrows:
        if row.run_id != budget.run_id:
            gaps.append(
                "selectivity.budget.subrow_run_id: budget subrow "
                f"{row.domain!r} uses run_id {row.run_id!r}, not the "
                f"table run_id {budget.run_id!r}"
            )
        if row.reused:
            if row.task_ref_count or row.step_count or row.qc_count:
                failures.append(
                    "selectivity.budget.reused_consumption: reused budget subrow "
                    f"{row.domain!r} must not consume new "
                    "TaskRef/step/QC counts"
                )
            if not row.reused_artifacts:
                failures.append(
                    "selectivity.budget.reused_artifacts: reused budget subrow "
                    f"{row.domain!r} must list the reused artifacts"
                )
    if budget.previous_run_ids:
        if budget.previous_cumulative is None:
            failures.append(
                "selectivity.budget.reset: previous_run_ids are recorded without "
                "previous_cumulative; the cumulative budget cannot be proven not to "
                "have been reset by a changed run_id"
            )
        if budget.run_id in budget.previous_run_ids:
            failures.append(
                "selectivity.budget.run_id_reused: the budget run_id is reused from "
                "a previous run; a changed run_id must never reset the cumulative "
                "budget"
            )
    totals = budget.totals()
    if (
        totals["task_refs"] != budget.cumulative_task_refs
        or totals["steps"] != budget.cumulative_steps
        or totals["qc_calls"] != budget.cumulative_qc_calls
    ):
        failures.append(
            "selectivity.budget.cumulative: the cumulative budget total does not "
            "equal the sum of the nmr/thermo/"
            "selectivity subrows"
        )
    if budget.previous_cumulative is not None:
        previous = budget.previous_cumulative
        for name, current in (
            ("task_refs", budget.cumulative_task_refs),
            ("steps", budget.cumulative_steps),
            ("qc_calls", budget.cumulative_qc_calls),
        ):
            prior = previous.get(name)
            if isinstance(prior, int) and current < prior:
                failures.append(
                    "selectivity.budget.decreased: the cumulative "
                    f"{name} decreased from {prior} to {current}; a new "
                    "run_id must not reset the cumulative budget"
                )
    selectivity_row = budget.subrow("selectivity")
    if selectivity_row is not None and selectivity_row.task_ref_count > 1:
        failures.append(
            "selectivity.budget.task_ref_cap: the selectivity subrow consumes more "
            "than one new TaskRef; the A-25a "
            "budget caps one new Local TaskRef"
        )
    return gaps, failures


def _validate_path(
    record: SelectivityPathRecord,
    *,
    fixture_manifest: Mapping[str, Any],
    b23b: B23bArtifacts | None,
    read_root: Path,
) -> SelectivityPathEvaluation:
    gaps: list[str] = []
    fixture_paths = _fixture_paths(fixture_manifest)
    fixture_path = fixture_paths.get(record.path_id)
    if fixture_path is None:
        return SelectivityPathEvaluation(
            path_id=record.path_id,
            qualified=False,
            status="not_evaluated",
            gaps=(f"{record.path_id} is not a declared fixture path",),
            claim_statuses={},
        )
    fixture_signature = _categorical_signature(
        _as_mapping(fixture_path.get("signature"), f"fixture.paths[{record.path_id}].signature")
    )
    actual_signature = _categorical_signature(record.endpoint_signature)
    if actual_signature != fixture_signature:
        gaps.append(
            "selectivity.path.signature: "
            f"{record.path_id} endpoint signature {actual_signature} does not match the "
            f"frozen fixture signature {fixture_signature}"
        )
    if (
        record.mapping_topology_digest
        != fixture_manifest["shared_left_side"]["mapping_topology_digest"]
    ):
        gaps.append(
            "selectivity.path.mapping: "
            f"{record.path_id} mapping topology digest differs from the frozen topology"
        )
    if record.product_digest != fixture_path.get("endpoint_digest"):
        gaps.append(
            "selectivity.path.product: "
            f"{record.path_id} product digest differs from the frozen endpoint digest"
        )
    expected_comparison = _expected_comparison(fixture_manifest)
    if dict(record.comparison_conditions) != expected_comparison:
        gaps.append(
            "selectivity.path.conditions: "
            f"{record.path_id} comparison conditions differ from the frozen conditions"
        )
    if b23b is None:
        gaps.append(
            "selectivity.path.authorized_missing: "
            f"{record.path_id}: the B23b authorized artifact set is absent"
        )
    else:
        authorized = b23b.authorized_for(record.path_id)
        if authorized is None:
            gaps.append(
                "selectivity.path.authorized_absent: "
                f"{record.path_id}: no B23b authorized artifact is listed"
            )
        else:
            if authorized.report_path != record.report_path:
                gaps.append(
                    "selectivity.path.authorized_path: "
                    f"{record.path_id}: path report {record.report_path!r} is not the "
                    f"B23b-authorized {authorized.report_path!r}"
                )
            if authorized.digest.value != record.report_digest.value:
                gaps.append(
                    "selectivity.path.authorized_digest: "
                    f"{record.path_id}: path report digest does not match the B23b "
                    "authorized digest"
                )
    report: EvidenceReport | None = None
    report_source: ArtifactSourceRef | None = None
    claim_statuses: dict[str, str] = {}
    try:
        resolved, actual = verify_artifact_digest(
            record.report_path, record.report_digest, read_root
        )
        report_payload = json.loads(resolved.read_text(encoding="utf-8"))
        report = evidence_report_from_dict(report_payload)
        report_source = _source(record.report_path, record.report_digest, "path_evidence")
        if report.execution.terminal_state != "completed":
            gaps.append(
                "selectivity.path.execution: "
                f"{record.path_id}: path evidence execution is "
                f"{report.execution.terminal_state!r}, not 'completed'"
            )
        if report.subject.subject_id != record.path_id:
            gaps.append(
                "selectivity.path.subject: "
                f"{record.path_id}: path evidence subject {report.subject.subject_id!r} "
                "does not identify this path"
            )
        verify_claim_sources(report, read_root)
        required = dict(REQUIRED_PATH_CLAIM_IDS)
        if record.free_energy_kcal_per_mol is not None:
            required[FREE_ENERGY_CLAIM_ID] = ACTIVATION_ENERGY_COMPARABILITY_VALIDATOR_ID
        for claim_id, expected_validator in required.items():
            claim = report.claim(claim_id)
            if claim is None:
                gaps.append(
                    "selectivity.path.claim: "
                    f"{record.path_id}: required claim {claim_id!r} is absent"
                )
                claim_statuses[claim_id] = "not_evaluated"
                continue
            claim_statuses[claim_id] = claim.status
            if claim.status != "supported":
                gaps.append(
                    "selectivity.path.claim: "
                    f"{record.path_id}: required claim {claim_id!r} is {claim.status!r}, "
                    "not 'supported'"
                )
            if claim.validator is None or claim.validator.validator_id != expected_validator:
                gaps.append(
                    "selectivity.path.claim_validator: "
                    f"{record.path_id}: claim {claim_id!r} does not carry the expected "
                    f"validator {expected_validator!r}"
                )
    except (OSError, ValueError, EvidenceContractError) as exc:
        gaps.append(
            "selectivity.path.unavailable: "
            f"{record.path_id}: path evidence is unavailable or invalid: {exc}"
        )
    qualified = not gaps
    status = "supported" if qualified else ("not_evaluated" if report is None else "inconclusive")
    return SelectivityPathEvaluation(
        path_id=record.path_id,
        qualified=qualified,
        status=status,
        gaps=tuple(gaps),
        claim_statuses=claim_statuses,
        report=report,
        report_source=report_source,
    )


# ── audit result ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SelectivityAuditResult:
    """The bounded EvidenceReport plus the separate platform/audit facts."""

    report: EvidenceReport
    constructed: bool
    real_run: bool
    platform_evidence_state: str
    platform_evidence_gaps: tuple[str, ...]
    h6_open: bool
    preference: str | None
    preference_delta_kcal_per_mol: float | None
    public_key: SelectivityPublicKey | None
    b23b_artifact_id: str | None
    b23b_readiness: str | None
    budget_summary: Mapping[str, Any]
    coverage: Mapping[str, Any]
    limitations: tuple[str, ...]
    gaps: tuple[str, ...]
    path_statuses: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.platform_evidence_state not in PLATFORM_EVIDENCE_STATES:
            raise SelectivityReferenceError(
                f"platform_evidence_state {self.platform_evidence_state!r} is invalid"
            )

    def claim_status(self, claim_id: str) -> str | None:
        """Return one claim status, or ``None`` when the claim is absent."""
        claim = self.report.claim(claim_id)
        return None if claim is None else claim.status

    @property
    def preference_status(self) -> str:
        """Return the scientific status of the relative-preference claim."""
        return self.claim_status("selectivity.preference") or "not_evaluated"

    @property
    def both_paths_qualified(self) -> bool:
        """Whether both declared paths qualified."""
        paths = self.coverage.get("paths", {})
        return bool(self.coverage.get("both_paths_qualified")) and all(
            bool(paths.get(path_id, {}).get("qualified")) for path_id in PATH_IDS
        )

    def _absent_producer_projection(self) -> dict[str, Any]:
        """Return the producer-input presence projection from the audited state."""
        reference_present = self.public_key is not None
        b23b_artifact_present = self.b23b_artifact_id is not None
        budget_table_present = bool(self.budget_summary)
        path_records_present = bool(self.coverage.get("path_records_present"))
        run_key_present = self.public_key is not None and bool(self.public_key.run_id)
        missing = [
            name
            for name, present in (
                ("reference", reference_present),
                ("b23b_artifact", b23b_artifact_present),
                ("budget_table", budget_table_present),
                ("path_records", path_records_present),
                ("run_key", run_key_present),
            )
            if not present
        ]
        if missing:
            note = (
                "Missing producer inputs: "
                + ", ".join(missing)
                + ". This report records the absent-producer state and is never "
                "upgraded by this file's presence."
            )
        else:
            note = (
                "All declared producer inputs are present; the real-run state is "
                "governed by the B23b real-QC statement and the path/budget audits."
            )
        return {
            "reference_present": reference_present,
            "b23b_artifact_present": b23b_artifact_present,
            "budget_table_present": budget_table_present,
            "path_records_present": path_records_present,
            "run_key_present": run_key_present,
            "note": note,
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the report plus the separate audit projection."""
        return {
            "report": self.report.to_dict(),
            "audit": {
                "constructed": self.constructed,
                "real_run": self.real_run,
                "platform_evidence_state": self.platform_evidence_state,
                "platform_evidence_gaps": list(self.platform_evidence_gaps),
                "h6_open": self.h6_open,
                "preference": self.preference,
                "preference_status": self.preference_status,
                "both_paths_qualified": self.both_paths_qualified,
                "preference_delta_kcal_per_mol": self.preference_delta_kcal_per_mol,
                "public_key": None if self.public_key is None else self.public_key.to_dict(),
                "b23b_artifact_id": self.b23b_artifact_id,
                "b23b_readiness": self.b23b_readiness,
                "budget": dict(self.budget_summary),
                "coverage": dict(self.coverage),
                "absent_producer": self._absent_producer_projection(),
                "limitations": list(self.limitations),
                "gaps": list(self.gaps),
                "path_statuses": dict(self.path_statuses),
            },
        }


# ── report builder ─────────────────────────────────────────────────────────


def build_selectivity_audit(
    *,
    fixture_manifest: Mapping[str, Any],
    reference_records: Sequence[Mapping[str, Any]] = (),
    b23b: Mapping[str, Any] | None = None,
    budget: Mapping[str, Any] | None = None,
    paths: Sequence[Mapping[str, Any]] = (),
    read_root: Path,
    c_t40b_signoff: Mapping[str, Any] | None = None,
    constructed: bool = False,
    report_id: str = "selectivity-a25b-report",
) -> SelectivityAuditResult:
    """Consume the selectivity reference, artifacts, budget and path evidence.

    The function is pure with respect to the platform: it reads the supplied
    artifacts below ``read_root`` and never submits a job, starts a scheduler
    or writes to the read root.  When the reference, B23b artifacts, budget or
    a path is absent, the platform state is ``NOT_VERIFIED`` and H6 stays open.
    """
    if not isinstance(fixture_manifest, Mapping):
        raise SelectivityReferenceError("fixture_manifest must be a JSON object")
    read_root = Path(read_root)
    parsed_b23b = None if b23b is None else B23bArtifacts.from_dict(b23b)
    if parsed_b23b is not None and parsed_b23b.constructed:
        constructed = True
    parsed_budget = None if budget is None else CrossDomainBudgetTable.from_dict(budget)
    parsed_paths: dict[str, SelectivityPathRecord] = {}
    path_record_gaps: list[str] = []
    for index, raw in enumerate(paths):
        try:
            record = SelectivityPathRecord.from_dict(raw, f"paths[{index}]")
            if record.path_id in parsed_paths:
                path_record_gaps.append(f"duplicate path record for {record.path_id}")
            parsed_paths[record.path_id] = record
        except (SelectivityReferenceError, ValueError) as exc:
            path_record_gaps.append(f"paths[{index}]: {exc}")

    public_key, reference_gaps = _validate_reference(reference_records)
    budget_gaps, budget_failures = _validate_budget(parsed_budget, public_key)
    if parsed_budget is not None:
        try:
            verify_artifact_digest(
                parsed_budget.record_path, parsed_budget.record_digest, read_root
            )
        except (OSError, EvidenceContractError, ValueError) as exc:
            budget_failures.append(
                f"selectivity.budget.unverifiable: the budget record is not verifiable: {exc}"
            )
    budget_valid = parsed_budget is not None and not budget_gaps and not budget_failures
    if parsed_b23b is None:
        reference_gaps.append(
            "selectivity.b23b.missing: the B23b artifact id is unavailable because no "
            "machine id was published for the declared edge "
            f"{B23B_EDGE_ARTIFACT_ID} and no authorized artifacts were exported"
        )
    else:
        if parsed_b23b.readiness != B23B_READINESS:
            reference_gaps.append(
                "selectivity.b23b.readiness: B23b readiness is "
                f"{parsed_b23b.readiness!r}, not {B23B_READINESS!r}"
            )
        if (
            parsed_b23b.run_id
            and public_key is not None
            and parsed_b23b.run_id != public_key.run_id
        ):
            reference_gaps.append(
                "selectivity.b23b.run_id: the B23b run_id differs from the public run_id"
            )
        if set(parsed_b23b.paths) != set(PATH_IDS):
            reference_gaps.append("selectivity.b23b.paths: B23b must list both declared paths")
        try:
            verify_artifact_digest(parsed_b23b.record_path, parsed_b23b.record_digest, read_root)
        except (OSError, EvidenceContractError, ValueError) as exc:
            reference_gaps.append(
                f"selectivity.b23b.unverifiable: the B23b record is not verifiable: {exc}"
            )

    evaluations: dict[str, SelectivityPathEvaluation] = {}
    for path_id in PATH_IDS:
        path_record = parsed_paths.get(path_id)
        if path_record is None:
            evaluations[path_id] = SelectivityPathEvaluation(
                path_id=path_id,
                qualified=False,
                status="not_evaluated",
                gaps=(f"{path_id} path record was not supplied",),
                claim_statuses={},
            )
            continue
        evaluations[path_id] = _validate_path(
            path_record,
            fixture_manifest=fixture_manifest,
            b23b=parsed_b23b,
            read_root=read_root,
        )

    # Build the claims with the numeric path records available for the preference.
    drafts: list[ClaimDraft] = []
    limitations = list(_LIMITATION_IDS)
    reference_ok = (
        public_key is not None
        and not reference_gaps
        and parsed_b23b is not None
        and parsed_b23b.readiness == B23B_READINESS
        and set(parsed_b23b.paths) >= set(PATH_IDS)
    )
    reference_sources: list[ArtifactSourceRef] = []
    if parsed_b23b is not None:
        reference_sources.append(
            _source(parsed_b23b.record_path, parsed_b23b.record_digest, "b23b_artifact_set")
        )
    if reference_ok:
        drafts.append(
            ClaimDraft(
                claim_id="selectivity.reference.registered",
                statement=(
                    "The selectivity result is keyed by the unique registered public "
                    "(owner, scenario, phase, run_id) reference and B23b exported the "
                    "authorized artifacts at integration_ready."
                ),
                status="supported",
                scope=ClaimScope(
                    subject="selectivity_reference",
                    applicability="system_specific",
                    conditions="registered experience:selectivity phase",
                    not_in_scope=("other subcases", "other scenarios"),
                ),
                source_refs=tuple(reference_sources),
                validator=_validator(_SELECTIVITY_REFERENCE_VALIDATOR_ID),
                limitations=_limits(constructed=constructed),
            )
        )
    else:
        missing: list[EvidenceMissingCheck] = []
        if public_key is None:
            missing.append(
                _missing("run_key_unavailable", "the registered public run key is absent")
            )
        if parsed_b23b is None:
            missing.append(
                _missing("external_artifact_unavailable", "the B23b artifact set is absent")
            )
        elif parsed_b23b.readiness != B23B_READINESS:
            missing.append(
                _missing(
                    "external_artifact_unavailable",
                    f"B23b readiness is {parsed_b23b.readiness!r}, not {B23B_READINESS!r}",
                )
            )
        if reference_gaps:
            missing.append(
                _missing(
                    "external_artifact_unavailable",
                    "; ".join(reference_gaps),
                    blocking=True,
                )
            )
        drafts.append(
            ClaimDraft(
                claim_id="selectivity.reference.registered",
                statement=(
                    "The registered public reference and B23b integration_ready "
                    "artifacts are required before a real selectivity result can be "
                    "audited."
                ),
                status="not_evaluated",
                scope=ClaimScope(
                    subject="selectivity_reference",
                    applicability="system_specific",
                    conditions="registered experience:selectivity phase",
                    not_in_scope=("other subcases", "other scenarios"),
                ),
                missing_checks=tuple(missing),
                limitations=_limits(
                    "lim-platform-not-verified", "lim-h6-open", constructed=constructed
                ),
            )
        )

    path_sources: dict[str, ArtifactSourceRef] = {}
    for path_id in PATH_IDS:
        evaluation = evaluations[path_id]
        sources: list[ArtifactSourceRef] = []
        if evaluation.report_source is not None:
            sources.append(evaluation.report_source)
            path_sources[path_id] = evaluation.report_source
        if evaluation.qualified:
            drafts.append(
                ClaimDraft(
                    claim_id=f"selectivity.{path_id}.qualified",
                    statement=(
                        f"The {path_id} path carries supported convergence, saddle-"
                        "candidate, bidirectional-connectivity and comparable-energy "
                        "evidence under the frozen conditions."
                    ),
                    status="supported",
                    scope=ClaimScope(
                        subject=f"{path_id}_qualified",
                        applicability="system_specific",
                        conditions="frozen A-25a comparison conditions",
                        not_in_scope=(
                            "other regioisomers",
                            "other conformers",
                            "experimental selectivity",
                        ),
                    ),
                    source_refs=tuple(sources),
                    validator=_validator(_SELECTIVITY_PATH_VALIDATOR_ID),
                    limitations=_limits(
                        "lim-single-conformer",
                        "lim-single-regioisomer",
                        constructed=constructed,
                    ),
                )
            )
        else:
            status = "not_evaluated" if evaluation.report is None else "inconclusive"
            if evaluation.report is None:
                check = _missing(
                    "external_artifact_unavailable",
                    f"{path_id} path evidence was not supplied or is invalid",
                )
            else:
                check = _missing(
                    "partial_output",
                    f"{path_id} path is not qualified: " + "; ".join(evaluation.gaps),
                )
            drafts.append(
                ClaimDraft(
                    claim_id=f"selectivity.{path_id}.qualified",
                    statement=(
                        f"The {path_id} path carries qualified convergence, saddle-"
                        "candidate, bidirectional-connectivity and comparable-energy "
                        "evidence under the frozen conditions."
                    ),
                    status=status,
                    scope=ClaimScope(
                        subject=f"{path_id}_qualified",
                        applicability="system_specific",
                        conditions="frozen A-25a comparison conditions",
                        not_in_scope=(
                            "other regioisomers",
                            "other conformers",
                            "experimental selectivity",
                        ),
                    ),
                    source_refs=tuple(sources),
                    missing_checks=(check,),
                    limitations=_limits(
                        "lim-single-conformer",
                        "lim-single-regioisomer",
                        constructed=constructed,
                    ),
                )
            )

    both_qualified = evaluations["path.endo"].qualified and evaluations["path.exo"].qualified
    preference: str | None = None
    delta: float | None = None
    preference_sources = tuple(
        source for source in (path_sources.get("path.endo"), path_sources.get("path.exo")) if source
    )
    raw_tolerances = fixture_manifest.get("tolerances")
    if not isinstance(raw_tolerances, Mapping):
        raise SelectivityReferenceError("fixture.tolerances must be a JSON object")
    tolerance = _as_finite_float(
        raw_tolerances.get("selectivity_preference_abs_kcal_per_mol"),
        "fixture.tolerances.selectivity_preference_abs_kcal_per_mol",
    )
    if tolerance <= 0.0:
        raise SelectivityReferenceError("fixture selectivity preference tolerance must be positive")
    if reference_ok and budget_valid and both_qualified:
        endo_record = parsed_paths.get("path.endo")
        exo_record = parsed_paths.get("path.exo")
        assert endo_record is not None and exo_record is not None
        delta = (
            endo_record.activation_energy_kcal_per_mol - exo_record.activation_energy_kcal_per_mol
        )
        if abs(delta) >= tolerance:
            preference = "endo" if delta < 0 else "exo"
            drafts.append(
                ClaimDraft(
                    claim_id="selectivity.preference",
                    statement=(
                        "Under the frozen conditions, "
                        f"{preference} has a lower electronic activation energy by "
                        f"{abs(delta):.3f} kcal/mol (relative preference, not an "
                        "experimental selectivity claim)."
                    ),
                    status="supported",
                    scope=ClaimScope(
                        subject="relative_activation_energy",
                        applicability="condition_specific",
                        conditions="same method, electronic state, temperature, pressure, "
                        "standard state, reference state and energy unit",
                        not_in_scope=(
                            "experimental selectivity",
                            "other regioisomers",
                            "complete mechanism",
                        ),
                    ),
                    source_refs=preference_sources,
                    validator=_validator(_SELECTIVITY_PREFERENCE_VALIDATOR_ID),
                    limitations=_limits(
                        "lim-relative-only", "lim-no-experimental", constructed=constructed
                    ),
                )
            )
        else:
            drafts.append(
                ClaimDraft(
                    claim_id="selectivity.preference",
                    statement=(
                        "The absolute relative activation energy is within the frozen "
                        f"{tolerance:.1f} kcal/mol tolerance; no relative preference is "
                        "stated."
                    ),
                    status="supported",
                    scope=ClaimScope(
                        subject="relative_activation_energy",
                        applicability="condition_specific",
                        conditions="same method, electronic state, temperature, pressure, "
                        "standard state, reference state and energy unit",
                        not_in_scope=("experimental selectivity", "other regioisomers"),
                    ),
                    source_refs=preference_sources,
                    limitations=_limits(
                        "lim-relative-only", "lim-no-experimental", constructed=constructed
                    ),
                )
            )
    else:
        missing_paths = [path_id for path_id in PATH_IDS if not evaluations[path_id].qualified]
        if not reference_ok or parsed_b23b is None or parsed_budget is None:
            status = "not_evaluated"
            reason = (
                "selectivity.preference.platform_unverified: the registered "
                "reference, B23b artifact set or cross-domain budget is absent: "
                + "; ".join([*reference_gaps, *budget_gaps])
            )
            extra_limits: tuple[str, ...] = ("lim-platform-not-verified", "lim-h6-open")
        elif budget_gaps or budget_failures:
            status = "inconclusive"
            reason = (
                "selectivity.preference.budget_unsupported: the cross-domain budget "
                "cannot support a preference: " + "; ".join([*budget_gaps, *budget_failures])
            )
            extra_limits = ("lim-platform-not-verified",)
        else:
            status = "inconclusive"
            reason = "selectivity.preference.path_unqualified: unqualified path(s): " + ", ".join(
                missing_paths
            )
            extra_limits = ()
        missing_check_id = (
            "external_artifact_unavailable" if status == "not_evaluated" else "partial_output"
        )
        drafts.append(
            ClaimDraft(
                claim_id="selectivity.preference",
                statement=(
                    "A relative endo/exo preference requires the registered reference, "
                    "both B23b-qualified paths and a valid cross-domain budget under the "
                    "frozen conditions; no preference is stated."
                ),
                status=status,
                scope=ClaimScope(
                    subject="relative_activation_energy",
                    applicability="condition_specific",
                    conditions="same method, electronic state, temperature, pressure, "
                    "standard state, reference state and energy unit",
                    not_in_scope=("experimental selectivity", "other regioisomers"),
                ),
                source_refs=preference_sources,
                missing_checks=(_missing(missing_check_id, reason),),
                limitations=_limits(
                    *extra_limits,
                    "lim-relative-only",
                    "lim-no-experimental",
                    constructed=constructed,
                ),
            )
        )

    if budget_valid:
        budget_record = parsed_budget
        assert budget_record is not None
        drafts.append(
            ClaimDraft(
                claim_id="selectivity.budget",
                statement=(
                    "The cross-domain budget records the nmr, thermo and selectivity "
                    "subrows with per-subrow TaskRef/step/QC counts, the cumulative "
                    "total and reused artifacts; the cumulative total is not reset by "
                    "a changed run_id."
                ),
                status="supported",
                scope=ClaimScope(
                    subject="cross_domain_budget",
                    applicability="system_specific",
                    conditions="B/C cross-domain cumulative budget",
                    not_in_scope=("new budget allocation",),
                ),
                source_refs=(
                    _source(
                        budget_record.record_path,
                        budget_record.record_digest,
                        "cross_domain_budget",
                    ),
                ),
                validator=_validator(_CROSS_DOMAIN_BUDGET_VALIDATOR_ID),
                limitations=_limits("lim-budget-unmeasured", constructed=constructed),
            )
        )
    else:
        budget_missing: list[EvidenceMissingCheck] = []
        if parsed_budget is None:
            budget_missing.append(
                _missing(
                    "external_artifact_unavailable",
                    "the B/C cross-domain budget table is absent",
                )
            )
        else:
            budget_missing.append(
                _missing(
                    "partial_output",
                    "the budget table is invalid: " + "; ".join([*budget_failures, *budget_gaps]),
                )
            )
        drafts.append(
            ClaimDraft(
                claim_id="selectivity.budget",
                statement=(
                    "The cross-domain budget must record the nmr, thermo and "
                    "selectivity subrows, per-subrow TaskRef/step/QC counts, the "
                    "cumulative total and reused artifacts without a run_id reset."
                ),
                status="not_evaluated" if parsed_budget is None else "inconclusive",
                scope=ClaimScope(
                    subject="cross_domain_budget",
                    applicability="system_specific",
                    conditions="B/C cross-domain cumulative budget",
                    not_in_scope=("new budget allocation",),
                ),
                missing_checks=tuple(budget_missing),
                limitations=_limits(
                    "lim-budget-unmeasured",
                    "lim-platform-not-verified",
                    constructed=constructed,
                ),
            )
        )

    drafts.append(
        ClaimDraft(
            claim_id="selectivity.experimental_prediction",
            statement=(
                "No experimental quantitative selectivity claim is made from this "
                "bounded computational report."
            ),
            status="not_evaluated",
            scope=ClaimScope(
                subject="experimental_selectivity",
                applicability="condition_specific",
                conditions="no experimental data",
                not_in_scope=("experimental rates", "experimental selectivity"),
            ),
            missing_checks=(
                _missing(
                    "not_executed",
                    "no experimental measurement or rate data exists in this report",
                ),
            ),
            limitations=_limits("lim-no-experimental", constructed=constructed),
        )
    )

    evidence_gaps = [
        *reference_gaps,
        *budget_gaps,
        *budget_failures,
        *[gap for evaluation in evaluations.values() for gap in evaluation.gaps],
    ]
    real_qc_binary_ran = bool(parsed_b23b is not None and parsed_b23b.real_qc_binary_ran)
    any_refuted = any(
        status == "refuted"
        for evaluation in evaluations.values()
        for status in evaluation.claim_statuses.values()
    )
    if constructed:
        platform_state = "NOT_VERIFIED"
        platform_gaps = [
            "selectivity.platform.constructed: constructed demonstration; no platform execution",
            *evidence_gaps,
        ]
    elif (
        any_refuted
        and reference_ok
        and parsed_budget is not None
        and not budget_gaps
        and not budget_failures
        and real_qc_binary_ran
    ):
        platform_state = "FAIL"
        platform_gaps = list(evidence_gaps)
    elif (
        not reference_ok
        or parsed_b23b is None
        or parsed_budget is None
        or budget_gaps
        or budget_failures
        or not real_qc_binary_ran
        or not both_qualified
    ):
        platform_state = "NOT_VERIFIED"
        platform_gaps = list(evidence_gaps)
        if not real_qc_binary_ran:
            if parsed_b23b is not None:
                platform_gaps.append(
                    "selectivity.platform.no_qc: B23b reports that no real QC binary ran"
                )
            else:
                platform_gaps.append(
                    "selectivity.platform.no_qc: no real QC binary ran and no B23b "
                    "export was supplied"
                )
        platform_gaps = list(dict.fromkeys(platform_gaps))
    else:
        platform_state = "PASS"
        platform_gaps = []

    signoff_ok = False
    if c_t40b_signoff is not None:
        signoff = _as_mapping(c_t40b_signoff, "c_t40b_signoff")
        signoff_ok = (
            bool(signoff.get("signed"))
            and bool(signoff.get("signed_after_audit"))
            and signoff.get("owner") == SELECTIVITY_OWNER
            and signoff.get("phase") == SELECTIVITY_PHASE
            and (public_key is None or signoff.get("run_id") in (None, public_key.run_id))
        )
    h6_open = not (platform_state == "PASS" and not constructed and both_qualified and signoff_ok)
    if h6_open:
        limitations.append("lim-h6-open")

    claim_limitations = tuple(sorted(set(limitations)))
    limitation_objects = tuple(
        Limitation(
            limitation_id=limitation_id,
            description=_LIMITATION_IDS[limitation_id],
            affected_claim_ids=tuple(
                draft.claim_id for draft in drafts if limitation_id in draft.limitations
            ),
        )
        for limitation_id in claim_limitations
    )
    execution = ExecutionFacts(
        terminal_state="completed",
        argv=("constructed-selectivity-audit",) if constructed else ("acp2-selectivity-audit",),
        exit_code=0,
        started_at=None,
        finished_at=None,
    )
    subject = Subject(
        subject_id=str(fixture_manifest.get("case_id", "selectivity-case")),
        subject_kind="artifact_set",
        description=(
            "Bounded selectivity audit over the A-25a frozen endo/exo case; "
            + ("constructed demonstration" if constructed else "platform artifacts")
        ),
        input_digests=tuple(source.digest for source in path_sources.values()),
    )
    report = assemble_evidence_report(
        report_id=report_id,
        subject=subject,
        execution=execution,
        drafts=drafts,
        limitations=limitation_objects,
    )
    budget_summary: dict[str, Any] = {}
    if parsed_budget is not None:
        budget_summary = {
            "run_id": parsed_budget.run_id,
            "subrows": [row.to_dict() for row in parsed_budget.subrows],
            "cumulative": {
                "task_refs": parsed_budget.cumulative_task_refs,
                "steps": parsed_budget.cumulative_steps,
                "qc_calls": parsed_budget.cumulative_qc_calls,
                "wall_seconds": parsed_budget.cumulative_wall_seconds,
            },
            "subrow_totals": parsed_budget.totals(),
            "reused_artifacts": sorted(
                {artifact for row in parsed_budget.subrows for artifact in row.reused_artifacts}
            ),
        }
    coverage = {
        "paths": {
            path_id: {
                "status": evaluations[path_id].status,
                "qualified": evaluations[path_id].qualified,
                "claim_statuses": dict(evaluations[path_id].claim_statuses),
                "gaps": list(evaluations[path_id].gaps),
            }
            for path_id in PATH_IDS
        },
        "both_paths_qualified": both_qualified,
        "required_path_claim_ids": sorted(REQUIRED_PATH_CLAIM_IDS),
        "free_energy_required": any(
            record.free_energy_kcal_per_mol is not None for record in parsed_paths.values()
        ),
        "budget_domains": list(BUDGET_DOMAINS),
        "budget_table_present": parsed_budget is not None,
        "path_records_present": bool(paths),
        "budget_requirement": _budget_requirement(fixture_manifest),
        "b23b_edge_artifact_id": B23B_EDGE_ARTIFACT_ID,
        "reused_artifacts": budget_summary.get("reused_artifacts", []),
    }
    gaps = [
        *reference_gaps,
        *budget_gaps,
        *budget_failures,
        *path_record_gaps,
        *[gap for evaluation in evaluations.values() for gap in evaluation.gaps],
    ]
    return SelectivityAuditResult(
        report=report,
        constructed=constructed,
        real_run=(not constructed and real_qc_binary_ran and platform_state in ("PASS", "FAIL")),
        platform_evidence_state=platform_state,
        platform_evidence_gaps=tuple(platform_gaps),
        h6_open=h6_open,
        preference=preference,
        preference_delta_kcal_per_mol=delta,
        public_key=public_key,
        b23b_artifact_id=None if parsed_b23b is None else parsed_b23b.artifact_id,
        b23b_readiness=None if parsed_b23b is None else parsed_b23b.readiness,
        budget_summary=budget_summary,
        coverage=coverage,
        limitations=claim_limitations,
        gaps=tuple(gaps),
        path_statuses={path_id: evaluations[path_id].status for path_id in PATH_IDS},
    )
