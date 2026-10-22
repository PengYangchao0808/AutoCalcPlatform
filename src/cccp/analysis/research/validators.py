"""Deterministic stationary-point science validators (A-11a).

This module turns an :class:`~cccp.calculation.results.TaskResult` pair — one
``optimize`` result and one ``frequency`` result — into the frozen four-valued
scientific claim vocabulary of ``acp2-contracts/science/evidence.schema.json``:

``supported`` | ``refuted`` | ``inconclusive`` | ``not_evaluated``

The checks answer one scientific question per claim:

* did the geometry optimization converge?
* was the frequency/Hessian calculation performed on the optimized geometry?
* was the frequency calculation performed with the same method and electronic
  state as the optimization?
* does the frequency result carry usable Hessian evidence at all?
* does the frequency list contain imaginary modes?
* is the candidate a first-order saddle *candidate* (exactly one imaginary
  mode) and is that specific mode identified by ORCA's native printed index
  plus provenance, never by an array position?
* does the mode evidence prove connectivity?  It never does — connectivity is
  a separate claim that requires bidirectional IRC endpoint evidence (A-16b)
  and is therefore ``inconclusive`` here.

Claim scope is deliberately narrow.  A non-converged optimization refutes
"this optimization converged"; it is not evidence that no stationary point
exists in the sampled region.  :func:`validate_no_stationary_point_exists`
therefore never returns ``refuted`` for a failed optimization — absence of a
stationary point is ``inconclusive``/``not_evaluated`` without stronger
evidence.

The distinction that the evidence contract requires is explicit in every
assessment's ``reason``:

* ``not_checked``      → ``not_evaluated`` — the check was not attempted or a
  prerequisite result is absent;
* ``missing_evidence`` → ``inconclusive`` — evidence exists but is partial or
  conflicting, so neither support nor refutation is claimed;
* ``contradiction``    → ``refuted`` — the evidence affirmatively contradicts
  the claim;
* ``affirmed``         → ``supported`` — the evidence affirms the claim within
  its declared scope.

An unconverged optimization can therefore never produce a ``supported``
minimum claim: convergence is evaluated first and any non-supporting outcome
short-circuits the composite verdict.

Everything here is pure and deterministic.  Module-level imports are standard
library only; the calculation/frequency types and the frozen tsmode
frequency classifier are imported lazily inside the functions that need them,
so importing this module never starts a subprocess and never pulls in numpy.

Author: QCcalc Team
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal, get_args

__all__ = [
    "ATOM_COUNT_TOLERANCE",
    "CLAIM_STATUSES",
    "COORDINATE_ABS_TOLERANCE_ANGSTROM",
    "ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID",
    "FREQUENCY_EVIDENCE_VALIDATOR_ID",
    "FREQUENCY_MEMBERSHIP_VALIDATOR_ID",
    "IMAGINARY_MODE_THRESHOLD_CM1",
    "LOW_FREQUENCY_THRESHOLD_CM1",
    "METHOD_CONSISTENCY_VALIDATOR_ID",
    "MODE_SELECTION_STATUSES",
    "OPTIMIZATION_CONVERGENCE_VALIDATOR_ID",
    "STATIONARY_POINT_EXISTENCE_VALIDATOR_ID",
    "STATIONARY_POINT_VALIDATOR_ID",
    "TS_CONNECTIVITY_VALIDATOR_ID",
    "TS_MODE_MAPPING_VALIDATOR_ID",
    "TS_SADDLE_CANDIDATE_VALIDATOR_ID",
    "VALIDATOR_VERSION",
    "AssessmentReason",
    "ClaimAssessment",
    "ClaimStatus",
    "MappedModeSelection",
    "MethodStateValidation",
    "MissingCheck",
    "StationaryPointValidation",
    "TransitionStateValidation",
    "validate_electronic_state_consistency",
    "validate_frequency_evidence",
    "validate_frequency_membership",
    "validate_method_consistency",
    "validate_method_state_consistency",
    "validate_no_stationary_point_exists",
    "validate_optimization_convergence",
    "validate_stationary_point",
    "validate_transition_state",
    "validate_ts_connectivity",
    "validate_ts_frequency_count",
    "validate_ts_mapped_mode",
]

# ── frozen validator identity ──────────────────────────────────────────────

VALIDATOR_VERSION: Final = "1.0.0"

OPTIMIZATION_CONVERGENCE_VALIDATOR_ID: Final = "check.optimization_convergence"
FREQUENCY_MEMBERSHIP_VALIDATOR_ID: Final = "check.frequency_membership"
FREQUENCY_EVIDENCE_VALIDATOR_ID: Final = "check.frequency_evidence"
METHOD_CONSISTENCY_VALIDATOR_ID: Final = "check.method_consistency"
ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID: Final = "check.electronic_state_consistency"
STATIONARY_POINT_VALIDATOR_ID: Final = "check.stationary_point"
STATIONARY_POINT_EXISTENCE_VALIDATOR_ID: Final = "check.stationary_point_existence"
TS_SADDLE_CANDIDATE_VALIDATOR_ID: Final = "check.ts_saddle_candidate"
TS_MODE_MAPPING_VALIDATOR_ID: Final = "check.ts_mode_mapping"
TS_CONNECTIVITY_VALIDATOR_ID: Final = "check.ts_connectivity"

#: Mapping statuses accepted by :class:`MappedModeSelection`.  Mirrors
#: ``cccp.analysis.tsmode.contracts.MappingStatus``; only ``resolved`` binds a
#: specific imaginary mode to a specific native printed index.
MODE_SELECTION_STATUSES: Final = frozenset(
    {"pending", "resolved", "ambiguous", "unsupported", "mismatch"}
)

# ── frozen numeric thresholds and tolerances ───────────────────────────────
#
# These constants are the single source of truth cited by
# ``tests/fixtures/acp2/science/optfreq/scientific-checks.json`` and its
# manifest.  Changing a value is a deliberate scientific-contract change and
# requires re-freezing the fixture before any real artifact validation.

#: A real mode at or below this wavenumber is reported as "soft": the minimum
#: is shallow and a reviewer should look at it, but a soft real mode is never a
#: refutation by itself.
LOW_FREQUENCY_THRESHOLD_CM1: Final = 50.0

#: The tsmode classifier's fixed imaginary-mode crossing (``frequency < 0``).
#: Recorded here so the fixture can cite the threshold that is actually used;
#: the classification itself is delegated to
#: :func:`cccp.analysis.tsmode.validation.validate_ts_frequencies`.
IMAGINARY_MODE_THRESHOLD_CM1: Final = 0.0

#: Two geometries are the same stationary-point structure when every Cartesian
#: component differs by no more than this value.  ORCA prints geometries at
#: 1e-6 A precision and reruns may introduce last-digit noise; 1e-3 A is far
#: below chemical significance while remaining above print precision.
COORDINATE_ABS_TOLERANCE_ANGSTROM: Final = 1.0e-3

#: Atom counts must agree exactly; a tolerance would hide a real mismatch.
ATOM_COUNT_TOLERANCE: Final = 0

# ── frozen claim vocabulary (mirrors evidence.schema.json) ─────────────────

ClaimStatus = Literal["supported", "refuted", "inconclusive", "not_evaluated"]
AssessmentReason = Literal["affirmed", "contradiction", "missing_evidence", "not_checked"]

#: Runtime view of the frozen status vocabulary.  ``question.CLAIM_STATUSES``
#: is the contract tuple; a test pins the two views together so neither can
#: drift silently.
CLAIM_STATUSES: Final = frozenset(get_args(ClaimStatus))

_REASON_FOR_STATUS: Final = {
    "supported": "affirmed",
    "refuted": "contradiction",
    "inconclusive": "missing_evidence",
    "not_evaluated": "not_checked",
}

#: Missing-check ids this module may emit.  A subset of the frozen
#: ``evidence.schema.json`` vocabulary; the test asserts containment.
_MISSING_CHECK_IDS: Final = frozenset(
    {
        "convergence_not_met",
        "electronic_state_unresolved",
        "frequency_not_computed",
        "geometry_not_stationary",
        "imaginary_mode_count",
        "imaginary_mode_missing",
        "method_identity_missing",
        "missing_irc",
        "partial_output",
        "not_executed",
        "validator_unavailable",
    }
)

_STATE_IDENTITY_KEYS: Final = (
    "state_id",
    "target_multiplicity",
    "spin_mode",
    "reference_multiplicity",
    "final_ms",
    "guess_strategy",
)


# ── result types ───────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MissingCheck:
    """One frozen-vocabulary missing/partial check with a human reason."""

    check_id: str
    reason: str
    blocking: bool = True

    def __post_init__(self) -> None:
        if self.check_id not in _MISSING_CHECK_IDS:
            raise ValueError(
                f"missing check id {self.check_id!r} is not in the frozen subset "
                f"{sorted(_MISSING_CHECK_IDS)}"
            )
        if not self.reason:
            raise ValueError("missing check requires a non-empty reason")

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped missing-check object."""
        return {
            "check_id": self.check_id,
            "reason": self.reason,
            "blocking": self.blocking,
        }


@dataclass(frozen=True, slots=True)
class ClaimAssessment:
    """One deterministic claim verdict with its evidence and missing checks.

    ``details`` carries JSON-safe, deterministic facts (thresholds, counts,
    digests) that a report builder can turn into source refs.  It is
    deliberately *not* a substitute for the digested ``source_refs`` required
    by the evidence schema for ``supported``/``refuted`` claims; todo 23 binds
    those artifacts.
    """

    claim_id: str
    statement: str
    status: ClaimStatus
    reason: AssessmentReason
    validator_id: str
    validator_version: str
    missing_checks: tuple[MissingCheck, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.claim_id:
            raise ValueError("claim_id must be non-empty")
        if not self.statement:
            raise ValueError("statement must be non-empty")
        if self.status not in CLAIM_STATUSES:
            raise ValueError(f"unknown claim status {self.status!r}")
        expected_reason = _REASON_FOR_STATUS[self.status]
        if self.reason != expected_reason:
            raise ValueError(
                f"status {self.status!r} requires reason {expected_reason!r}, got {self.reason!r}"
            )
        if not self.validator_id.startswith("check."):
            raise ValueError(
                f"deterministic validator id must start with 'check.', got {self.validator_id!r}"
            )
        if not self.validator_version or not self.validator_version[0].isdigit():
            raise ValueError("validator_version must start with a digit")
        object.__setattr__(self, "missing_checks", tuple(self.missing_checks))
        if self.status in ("inconclusive", "not_evaluated") and not self.missing_checks:
            raise ValueError(f"status {self.status!r} must list at least one missing check")
        object.__setattr__(self, "details", dict(self.details))
        object.__setattr__(self, "notes", tuple(str(note) for note in self.notes))

    def to_dict(self) -> dict[str, Any]:
        """Return the deterministic, JSON-safe claim view."""
        return {
            "claim_id": self.claim_id,
            "statement": self.statement,
            "status": self.status,
            "reason": self.reason,
            "validator": {
                "validator_id": self.validator_id,
                "validator_version": self.validator_version,
                "kind": "deterministic",
            },
            "missing_checks": [check.to_dict() for check in self.missing_checks],
            "details": dict(self.details),
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class MethodStateValidation:
    """The two independent comparability checks between optimize and frequency."""

    method: ClaimAssessment
    electronic_state: ClaimAssessment

    @property
    def claims(self) -> tuple[ClaimAssessment, ClaimAssessment]:
        """Both claims in report order."""
        return (self.method, self.electronic_state)

    @property
    def status(self) -> ClaimStatus:
        """Combined status: refuted dominates, then missing evidence."""
        statuses = (self.method.status, self.electronic_state.status)
        if "refuted" in statuses:
            return "refuted"
        if all(status == "supported" for status in statuses):
            return "supported"
        if "inconclusive" in statuses:
            return "inconclusive"
        return "not_evaluated"

    def to_dict(self) -> dict[str, Any]:
        """Return both claims plus the combined status."""
        return {
            "status": self.status,
            "claims": [claim.to_dict() for claim in self.claims],
        }


@dataclass(frozen=True, slots=True)
class StationaryPointValidation:
    """Composite stationary-point verdict plus every supporting claim."""

    claims: tuple[ClaimAssessment, ...]
    imaginary_mode_count: int | None = None
    mode_count: int | None = None
    soft_modes_cm1: tuple[float, ...] = ()
    optimized_structure_digest: str | None = None
    frequency_structure_digest: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(self, "soft_modes_cm1", tuple(float(f) for f in self.soft_modes_cm1))
        if not self.claims:
            raise ValueError("stationary-point validation requires at least one claim")

    @property
    def minimum(self) -> ClaimAssessment:
        """The composite "this geometry is a minimum" claim."""
        claim = self.claim("c-stationary-point-minimum")
        if claim is None:  # pragma: no cover - construction invariant
            raise ValueError("stationary-point validation has no minimum claim")
        return claim

    @property
    def status(self) -> ClaimStatus:
        """Overall verdict of the composite minimum claim."""
        return self.minimum.status

    def claim(self, claim_id: str) -> ClaimAssessment | None:
        """Return the claim with *claim_id*, or ``None`` when absent."""
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe composite report."""
        return {
            "status": self.status,
            "claim_id": self.minimum.claim_id,
            "claims": [claim.to_dict() for claim in self.claims],
            "imaginary_mode_count": self.imaginary_mode_count,
            "mode_count": self.mode_count,
            "soft_modes_cm1": list(self.soft_modes_cm1),
            "optimized_structure_digest": self.optimized_structure_digest,
            "frequency_structure_digest": self.frequency_structure_digest,
            "thresholds": {
                "low_frequency_threshold_cm1": LOW_FREQUENCY_THRESHOLD_CM1,
                "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
                "coordinate_abs_tolerance_angstrom": COORDINATE_ABS_TOLERANCE_ANGSTROM,
                "atom_count_tolerance": ATOM_COUNT_TOLERANCE,
            },
            "validator_version": VALIDATOR_VERSION,
        }


@dataclass(frozen=True, slots=True)
class MappedModeSelection:
    """The specific imaginary mode a TS candidate claims, by native identity.

    ``native_printed_index`` is ORCA's printed mode index (for example the
    ``6`` in ``6: -520.00 cm**-1``).  It is not an array position.  The
    optional fields bind the selection to the tsmode provenance chain so a
    report can cite the producing step/artifact rather than a list offset.
    """

    native_printed_index: int
    mapping_status: str = "resolved"
    target_mode_id: str | None = None
    source_step: str | None = None
    source_artifact_digest: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.native_printed_index, bool) or not isinstance(
            self.native_printed_index, int
        ):
            raise ValueError("native_printed_index must be an integer native printed mode index")
        if self.native_printed_index < 0:
            raise ValueError("native_printed_index must be >= 0")
        if self.mapping_status not in MODE_SELECTION_STATUSES:
            raise ValueError(
                f"mapping_status {self.mapping_status!r} is not one of "
                f"{sorted(MODE_SELECTION_STATUSES)}"
            )
        for name in ("target_mode_id", "source_step", "source_artifact_digest"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} must be a non-empty string when supplied")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe selection record."""
        return {
            "native_printed_index": self.native_printed_index,
            "mapping_status": self.mapping_status,
            "target_mode_id": self.target_mode_id,
            "source_step": self.source_step,
            "source_artifact_digest": self.source_artifact_digest,
        }


@dataclass(frozen=True, slots=True)
class TransitionStateValidation:
    """Composite first-order-saddle-candidate verdict.

    This is deliberately *not* a connectivity verdict: ``status`` only says
    whether the frequency evidence is consistent with a local first-order
    saddle candidate at the reported geometry.  :func:`validate_ts_connectivity`
    is the separate, always-more-conservative connectivity claim.
    """

    claims: tuple[ClaimAssessment, ...]
    classification: str | None = None
    imaginary_mode_count: int | None = None
    mode_count: int | None = None
    imaginary_modes_cm1: tuple[float, ...] = ()
    mapped_mode_index: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "claims", tuple(self.claims))
        object.__setattr__(
            self, "imaginary_modes_cm1", tuple(float(v) for v in self.imaginary_modes_cm1)
        )
        if not self.claims:
            raise ValueError("transition-state validation requires at least one claim")

    @property
    def candidate(self) -> ClaimAssessment:
        """The composite first-order-saddle-candidate claim."""
        claim = self.claim("c-ts-first-order-saddle-candidate")
        if claim is None:  # pragma: no cover - construction invariant
            raise ValueError("transition-state validation has no candidate claim")
        return claim

    @property
    def status(self) -> ClaimStatus:
        """Overall verdict of the composite saddle-candidate claim."""
        return self.candidate.status

    def claim(self, claim_id: str) -> ClaimAssessment | None:
        """Return the claim with *claim_id*, or ``None`` when absent."""
        for claim in self.claims:
            if claim.claim_id == claim_id:
                return claim
        return None

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe composite report."""
        return {
            "status": self.status,
            "claim_id": self.candidate.claim_id,
            "claims": [claim.to_dict() for claim in self.claims],
            "classification": self.classification,
            "imaginary_mode_count": self.imaginary_mode_count,
            "mode_count": self.mode_count,
            "imaginary_modes_cm1": list(self.imaginary_modes_cm1),
            "mapped_mode_index": self.mapped_mode_index,
            "thresholds": {
                "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
            },
            "validator_version": VALIDATOR_VERSION,
        }


# ── assessment construction helpers ────────────────────────────────────────


def _supported(
    claim_id: str,
    statement: str,
    validator_id: str,
    *,
    details: dict[str, Any] | None = None,
    notes: tuple[str, ...] = (),
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=claim_id,
        statement=statement,
        status="supported",
        reason="affirmed",
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        details=details or {},
        notes=notes,
    )


def _refuted(
    claim_id: str,
    statement: str,
    validator_id: str,
    *,
    details: dict[str, Any] | None = None,
    notes: tuple[str, ...] = (),
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=claim_id,
        statement=statement,
        status="refuted",
        reason="contradiction",
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        details=details or {},
        notes=notes,
    )


def _inconclusive(
    claim_id: str,
    statement: str,
    validator_id: str,
    check_id: str,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
    notes: tuple[str, ...] = (),
    blocking: bool = True,
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=claim_id,
        statement=statement,
        status="inconclusive",
        reason="missing_evidence",
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        missing_checks=(MissingCheck(check_id, reason, blocking),),
        details=details or {},
        notes=notes,
    )


def _not_evaluated(
    claim_id: str,
    statement: str,
    validator_id: str,
    check_id: str,
    reason: str,
    *,
    details: dict[str, Any] | None = None,
    notes: tuple[str, ...] = (),
    blocking: bool = True,
) -> ClaimAssessment:
    return ClaimAssessment(
        claim_id=claim_id,
        statement=statement,
        status="not_evaluated",
        reason="not_checked",
        validator_id=validator_id,
        validator_version=VALIDATOR_VERSION,
        missing_checks=(MissingCheck(check_id, reason, blocking),),
        details=details or {},
        notes=notes,
    )


# ── TaskResult duck-typed accessors (no calculation-layer import) ──────────


def _task_name(result: Any | None) -> str | None:
    if result is None:
        return None
    task = getattr(result, "task", None)
    if task is None:
        return None
    value = getattr(task, "value", task)
    return str(value)


def _status_name(result: Any | None) -> str | None:
    if result is None:
        return None
    value = getattr(result, "status", None)
    return str(value) if value is not None else None


def _error_kind(result: Any | None) -> str | None:
    if result is None:
        return None
    kind = getattr(result, "error_kind", None)
    if kind is None:
        return None
    value = getattr(kind, "value", kind)
    return str(value)


def _payload(result: Any | None) -> Any | None:
    return getattr(result, "payload", None) if result is not None else None


def _provenance_method(result: Any | None) -> str | None:
    provenance = getattr(result, "provenance", None) if result is not None else None
    method = getattr(provenance, "method", None)
    if isinstance(method, str) and method.strip():
        return method.strip()
    return None


def _provenance_backend(result: Any | None) -> str | None:
    provenance = getattr(result, "provenance", None) if result is not None else None
    backend = getattr(provenance, "backend", None)
    return str(backend) if isinstance(backend, str) and backend else None


def _electronic_state(result: Any | None) -> dict[str, Any] | None:
    payload = _payload(result)
    state = getattr(payload, "electronic_state", None)
    if isinstance(state, dict):
        return dict(state)
    return None


def _geometry(result: Any | None) -> tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None:
    if result is None:
        return None
    raw_symbols = getattr(result, "symbols", None)
    raw_coordinates = getattr(result, "coordinates", None)
    if not raw_symbols or raw_coordinates is None:
        return None
    symbols = tuple(str(symbol) for symbol in raw_symbols)
    coordinates: list[tuple[float, ...]] = []
    for row in raw_coordinates:
        try:
            values = tuple(float(value) for value in row)
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or any(not math.isfinite(value) for value in values):
            return None
        coordinates.append(values)
    if len(coordinates) != len(symbols):
        return None
    return symbols, tuple(coordinates)


def _structure_digest(
    geometry: tuple[tuple[str, ...], tuple[tuple[float, ...], ...]] | None,
) -> str | None:
    if geometry is None:
        return None
    symbols, coordinates = geometry
    try:
        from cccp.analysis.tsmode.contracts import optimized_structure_digest
    except ImportError:  # pragma: no cover - tsmode ships with cccp
        return None
    try:
        return optimized_structure_digest(list(symbols), [list(row) for row in coordinates])
    except (TypeError, ValueError):
        return None


def _frequency_list(result: Any | None) -> tuple[float, ...] | None:
    if result is None:
        return None
    raw = getattr(result, "frequencies", None)
    if raw is None:
        return None
    try:
        values = tuple(float(value) for value in raw)
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) for value in values):
        return None
    return values


def _state_identity_view(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    view: dict[str, Any] = {}
    for key in _STATE_IDENTITY_KEYS:
        value = state.get(key)
        if value is not None:
            view[key] = value
    return view or None


def _imaginary_classification(frequencies: tuple[float, ...]) -> Any:
    """Delegate imaginary-mode classification to the frozen tsmode validator."""
    from cccp.analysis.tsmode.validation import validate_ts_frequencies

    return validate_ts_frequencies([float(value) for value in frequencies])


def _frequency_analysis(result: Any | None) -> Any | None:
    payload = _payload(result)
    return getattr(payload, "analysis", None) if payload is not None else None


def _mode_frequency_map(result: Any | None) -> dict[int, float] | None:
    """Native printed mode index → frequency, or ``None`` when unavailable."""
    analysis = _frequency_analysis(result)
    raw = getattr(analysis, "mode_frequencies", None)
    if not isinstance(raw, Mapping) or not raw:
        return None
    frequencies: dict[int, float] = {}
    for key, value in raw.items():
        if isinstance(key, bool):
            return None
        try:
            index = int(key)
            frequency = float(value)
        except (TypeError, ValueError):
            return None
        if index < 0 or not math.isfinite(frequency):
            return None
        frequencies[index] = frequency
    return frequencies


def _mode_vector_map(result: Any | None) -> dict[int, Any] | None:
    analysis = _frequency_analysis(result)
    raw = getattr(analysis, "mode_vectors", None)
    if not isinstance(raw, Mapping):
        return None
    vectors: dict[int, Any] = {}
    for key, value in raw.items():
        if isinstance(key, bool):
            return None
        try:
            vectors[int(key)] = value
        except (TypeError, ValueError):
            return None
    return vectors


def _require_mode_index(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "native_printed_index must be an integer native printed mode index, "
            f"got {type(value).__name__}"
        )
    if value < 0:
        raise ValueError("native_printed_index must be >= 0")
    return value


def _optional_nonempty_str(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("mode-selection provenance fields must be non-empty strings")
    return value


def _coerce_mapped_mode(value: Any) -> MappedModeSelection | None:
    """Accept a :class:`MappedModeSelection` or an equivalent record.

    A mapping carrying only ``index`` is rejected: that key is the array
    position trap this validator exists to catch.  ``source_mode_index`` is
    accepted as the tsmode contract's explicit alias for the native printed
    index.
    """
    if value is None:
        return None
    if isinstance(value, MappedModeSelection):
        return value
    if isinstance(value, Mapping):
        if (
            "native_printed_index" not in value
            and "source_mode_index" not in value
            and "index" in value
        ):
            raise ValueError(
                "mode selection must identify the mode by native_printed_index, "
                "not by an array index"
            )
        raw_index = value.get("native_printed_index", value.get("source_mode_index"))
        if raw_index is None:
            raise ValueError("mode selection requires native_printed_index")
        raw_status = value.get("mapping_status", "resolved")
        if not isinstance(raw_status, str):
            raise ValueError("mapping_status must be a string")
        return MappedModeSelection(
            native_printed_index=_require_mode_index(raw_index),
            mapping_status=raw_status,
            target_mode_id=_optional_nonempty_str(value.get("target_mode_id")),
            source_step=_optional_nonempty_str(value.get("source_step")),
            source_artifact_digest=_optional_nonempty_str(value.get("source_artifact_digest")),
        )

    raw_index = getattr(value, "native_printed_index", None)
    if raw_index is None:
        raw_index = getattr(value, "source_mode_index", None)
    if raw_index is None:
        if getattr(value, "index", None) is not None:
            raise ValueError(
                "mode selection must identify the mode by native_printed_index, "
                "not by an array index"
            )
        raise ValueError("mode selection requires native_printed_index")
    raw_status = getattr(value, "mapping_status", "resolved")
    if not isinstance(raw_status, str):
        raise ValueError("mapping_status must be a string")
    return MappedModeSelection(
        native_printed_index=_require_mode_index(raw_index),
        mapping_status=raw_status,
        target_mode_id=_optional_nonempty_str(getattr(value, "target_mode_id", None)),
        source_step=_optional_nonempty_str(getattr(value, "source_step", None)),
        source_artifact_digest=_optional_nonempty_str(
            getattr(value, "source_artifact_digest", None)
        ),
    )


# ── individual validators ──────────────────────────────────────────────────


def validate_optimization_convergence(
    optimize_result: Any | None,
) -> ClaimAssessment:
    """Decide whether an ``optimize`` result declares convergence.

    The calculation contract is authoritative: a completed
    :class:`~cccp.calculation.results.TaskResult` with an
    :class:`~cccp.calculation.results.OptimizePayload` whose
    ``optimization_status`` is ``"converged"`` supports convergence.  An
    explicit non-converged declaration, ``error_kind == "not_converged"`` or a
    ``geometry_not_converged`` rescue diagnosis refutes it.  A missing result
    or a non-optimize task is ``not_evaluated``; a failed run without a
    convergence diagnosis is ``inconclusive`` (missing evidence), never
    support.

    Args:
        optimize_result: The ``optimize`` :class:`TaskResult`, or ``None``.

    Returns:
        A four-valued :class:`ClaimAssessment`.
    """
    claim_id = "c-optimization-converged"
    statement = "The geometry optimization converged to a stationary geometry."
    validator_id = OPTIMIZATION_CONVERGENCE_VALIDATOR_ID

    if optimize_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no optimization result was supplied",
        )
    task = _task_name(optimize_result)
    if task != "optimize":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"result task is {task!r}, not 'optimize'",
            details={"task": task},
        )

    payload = _payload(optimize_result)
    optimization_status = getattr(payload, "optimization_status", None)
    task_status = _status_name(optimize_result)
    error_kind = _error_kind(optimize_result)
    rescue_failure = getattr(payload, "rescue_failure_type", None)
    details: dict[str, Any] = {
        "task_status": task_status,
        "optimization_status": optimization_status,
        "error_kind": error_kind,
        "rescue_failure_type": rescue_failure,
        "claim_scope": "the reported optimization run only",
        "does_not_establish": ("existence or absence of a stationary point in the sampled region"),
    }

    if optimization_status == "converged":
        if task_status == "completed":
            return _supported(claim_id, statement, validator_id, details=details)
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "convergence_not_met",
            "the payload declares convergence but the task did not complete successfully",
            details=details,
        )

    if optimization_status is not None:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                f"the optimization result declares {optimization_status!r}",
                "This refutes convergence of this optimization only; it is not "
                "evidence that no stationary point exists in the sampled region.",
            ),
        )

    if error_kind == "not_converged" or rescue_failure == "geometry_not_converged":
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                "the optimization result explicitly reports a convergence failure",
                "This refutes convergence of this optimization only; it is not "
                "evidence that no stationary point exists in the sampled region.",
            ),
        )

    if task_status == "completed":
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "convergence_not_met",
            "the completed optimization result declares no convergence state",
            details=details,
        )

    return _inconclusive(
        claim_id,
        statement,
        validator_id,
        "partial_output",
        f"optimization task status {task_status!r} does not establish convergence",
        details=details,
    )


def validate_frequency_membership(
    optimize_result: Any | None,
    frequency_result: Any | None,
) -> ClaimAssessment:
    """Decide whether the frequency result belongs to the optimized structure.

    The comparison is order-preserving: symbols must match exactly and every
    Cartesian component must agree within
    :data:`COORDINATE_ABS_TOLERANCE_ANGSTROM`.  A mismatch beyond tolerance is
    a contradiction (``refuted``); a result that carries no geometry is missing
    evidence (``inconclusive``); an absent or wrong-task result is
    ``not_evaluated``.
    """
    claim_id = "c-frequency-belongs-to-optimized-structure"
    statement = "The frequency/Hessian calculation was performed on the optimized structure."
    validator_id = FREQUENCY_MEMBERSHIP_VALIDATOR_ID

    if optimize_result is None or frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "both an optimize result and a frequency result are required",
        )
    optimize_task = _task_name(optimize_result)
    frequency_task = _task_name(frequency_result)
    if optimize_task != "optimize" or frequency_task != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"unexpected result tasks: optimize={optimize_task!r}, frequency={frequency_task!r}",
            details={"optimize_task": optimize_task, "frequency_task": frequency_task},
        )

    optimized = _geometry(optimize_result)
    frequency = _geometry(frequency_result)
    if optimized is None or frequency is None:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "partial_output",
            "at least one result carries no usable coordinates/symbols",
            details={
                "optimize_geometry_present": optimized is not None,
                "frequency_geometry_present": frequency is not None,
            },
        )

    optimize_symbols, optimize_coordinates = optimized
    frequency_symbols, frequency_coordinates = frequency
    same_atom_count = len(optimize_symbols) == len(frequency_symbols)
    max_delta = (
        _max_component_delta(optimize_coordinates, frequency_coordinates)
        if same_atom_count
        else None
    )
    details: dict[str, Any] = {
        "atom_count": len(optimize_symbols),
        "coordinate_abs_tolerance_angstrom": COORDINATE_ABS_TOLERANCE_ANGSTROM,
        "max_component_delta_angstrom": max_delta,
        "optimized_structure_digest": _structure_digest(optimized),
        "frequency_structure_digest": _structure_digest(frequency),
    }

    if not same_atom_count:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(f"atom count differs ({len(optimize_symbols)} vs {len(frequency_symbols)})",),
        )
    if optimize_symbols != frequency_symbols:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=("element symbols/order differ between the two results",),
        )
    if max_delta is not None and max_delta > COORDINATE_ABS_TOLERANCE_ANGSTROM:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                "coordinate difference exceeds the frozen "
                f"{COORDINATE_ABS_TOLERANCE_ANGSTROM} A tolerance",
            ),
        )
    return _supported(claim_id, statement, validator_id, details=details)


def validate_frequency_evidence(frequency_result: Any | None) -> ClaimAssessment:
    """Decide whether the frequency result carries Hessian/normal-mode evidence.

    A frequency list without a parsed normal-mode analysis is *not* evidence of
    a stationary point: the Hessian-backed mode vectors are required.  Missing
    vectors are therefore ``inconclusive`` (``frequency_not_computed``), never
    ``supported``.
    """
    claim_id = "c-frequency-hessian-present"
    statement = "The frequency result carries parsed Hessian/normal-mode evidence for the modes."
    validator_id = FREQUENCY_EVIDENCE_VALIDATOR_ID

    if frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no frequency result was supplied",
        )
    task = _task_name(frequency_result)
    if task != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"result task is {task!r}, not 'frequency'",
            details={"task": task},
        )

    frequencies = _frequency_list(frequency_result)
    if not frequencies:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "frequency_not_computed",
            "the frequency result carries no vibrational frequencies",
        )

    payload = _payload(frequency_result)
    analysis = getattr(payload, "analysis", None)
    if analysis is None:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "frequency_not_computed",
            "no parsed frequency/Hessian analysis is attached to the result",
            details={"frequency_count": len(frequencies)},
        )

    mode_vectors = getattr(analysis, "mode_vectors", None) or {}
    if not mode_vectors:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "frequency_not_computed",
            "the Hessian normal-mode vectors are absent",
            details={"frequency_count": len(frequencies)},
        )

    mode_frequencies = getattr(analysis, "mode_frequencies", None) or {}
    expected_indices = sorted(
        index
        for index, value in mode_frequencies.items()
        if abs(float(value)) > IMAGINARY_MODE_THRESHOLD_CM1
    )
    missing_vectors = [index for index in expected_indices if index not in mode_vectors]
    symbols = getattr(frequency_result, "symbols", None)
    wrong_size = []
    if symbols:
        wrong_size = [index for index, rows in mode_vectors.items() if len(rows) != len(symbols)]
    details: dict[str, Any] = {
        "frequency_count": len(frequencies),
        "mode_vector_count": len(mode_vectors),
        "expected_mode_indices": expected_indices,
        "missing_mode_indices": missing_vectors,
        "wrong_atom_count_mode_indices": wrong_size,
    }
    if missing_vectors:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "partial_output",
            f"normal-mode vectors are missing for mode indices {missing_vectors}",
            details=details,
        )
    if wrong_size:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "partial_output",
            f"normal-mode vectors have the wrong atom count for indices {wrong_size}",
            details=details,
        )
    return _supported(claim_id, statement, validator_id, details=details)


def validate_method_consistency(
    optimize_result: Any | None,
    frequency_result: Any | None,
) -> ClaimAssessment:
    """Decide whether both results declare the same electronic-structure method."""
    claim_id = "c-method-consistent"
    statement = "The optimization and frequency calculations used the same method."
    validator_id = METHOD_CONSISTENCY_VALIDATOR_ID

    if optimize_result is None or frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "both an optimize result and a frequency result are required",
        )
    if _task_name(optimize_result) != "optimize" or _task_name(frequency_result) != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "method comparison requires one optimize and one frequency result",
        )

    optimize_method = _provenance_method(optimize_result)
    frequency_method = _provenance_method(frequency_result)
    details: dict[str, Any] = {
        "optimize_method": optimize_method,
        "frequency_method": frequency_method,
        "optimize_backend": _provenance_backend(optimize_result),
        "frequency_backend": _provenance_backend(frequency_result),
    }
    if optimize_method is None or frequency_method is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "method_identity_missing",
            "at least one result does not declare a method identity",
            details=details,
        )
    if optimize_method != frequency_method:
        return _refuted(claim_id, statement, validator_id, details=details)
    return _supported(claim_id, statement, validator_id, details=details)


def validate_electronic_state_consistency(
    optimize_result: Any | None,
    frequency_result: Any | None,
) -> ClaimAssessment:
    """Decide whether both results declare the same electronic state.

    ``not_evaluated`` with ``state_declared_on == "neither"`` means no state
    was declared on either side: there is nothing to compare and the composite
    minimum claim records that as a limitation instead of pretending the state
    was checked.  A state declared on only one side is missing evidence
    (``inconclusive``); two declared-but-different states are a contradiction.
    """
    claim_id = "c-electronic-state-consistent"
    statement = "The optimization and frequency calculations used the same electronic state."
    validator_id = ELECTRONIC_STATE_CONSISTENCY_VALIDATOR_ID

    if optimize_result is None or frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "both an optimize result and a frequency result are required",
        )
    if _task_name(optimize_result) != "optimize" or _task_name(frequency_result) != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "state comparison requires one optimize and one frequency result",
        )

    optimize_state = _state_identity_view(_electronic_state(optimize_result))
    frequency_state = _state_identity_view(_electronic_state(frequency_result))
    details: dict[str, Any] = {
        "optimize_state": optimize_state,
        "frequency_state": frequency_state,
        "state_declared_on": _state_declaration_scope(optimize_state, frequency_state),
    }

    if optimize_state is None and frequency_state is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "electronic_state_unresolved",
            "neither result declares an electronic-state identity",
            details=details,
        )
    if optimize_state is None or frequency_state is None:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "electronic_state_unresolved",
            "an electronic-state identity is declared on only one side",
            details=details,
        )

    common = sorted(set(optimize_state) & set(frequency_state))
    details["compared_state_fields"] = common
    if not common:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "electronic_state_unresolved",
            "the two electronic-state blocks share no comparable field",
            details=details,
        )
    differing = [
        field_name
        for field_name in common
        if optimize_state[field_name] != frequency_state[field_name]
    ]
    details["differing_state_fields"] = differing
    if differing:
        return _refuted(claim_id, statement, validator_id, details=details)
    return _supported(claim_id, statement, validator_id, details=details)


def validate_method_state_consistency(
    optimize_result: Any | None,
    frequency_result: Any | None,
) -> MethodStateValidation:
    """Run the method and electronic-state consistency checks together."""
    return MethodStateValidation(
        method=validate_method_consistency(optimize_result, frequency_result),
        electronic_state=validate_electronic_state_consistency(optimize_result, frequency_result),
    )


def validate_ts_frequency_count(frequency_result: Any | None) -> ClaimAssessment:
    """Decide whether a frequency list has exactly one imaginary mode.

    The classification is delegated to the frozen tsmode classifier
    (:func:`cccp.analysis.tsmode.validation.validate_ts_frequencies`), so the
    fixed zero crossing and the "every negative mode counts" rule are reused,
    not re-implemented.  Exactly one imaginary mode supports only a *local
    first-order saddle candidate*; zero or several imaginary modes refute that
    candidate claim.

    Args:
        frequency_result: The ``frequency`` :class:`TaskResult`, or ``None``.

    Returns:
        A four-valued :class:`ClaimAssessment`.
    """
    claim_id = "c-ts-exactly-one-imaginary-mode"
    statement = (
        "The frequency result contains exactly one imaginary mode, consistent "
        "with a first-order saddle candidate at the reported geometry."
    )
    validator_id = TS_SADDLE_CANDIDATE_VALIDATOR_ID

    if frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no frequency result was supplied",
        )
    task = _task_name(frequency_result)
    if task != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"result task is {task!r}, not 'frequency'",
            details={"task": task},
        )
    frequencies = _frequency_list(frequency_result)
    if not frequencies:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "frequency_not_computed",
            "the frequency result carries no vibrational frequencies",
        )

    validation = _imaginary_classification(frequencies)
    imaginary = tuple(float(value) for value in validation.all_imaginary_cm1)
    details: dict[str, Any] = {
        "classification": validation.classification,
        "imaginary_mode_count": len(imaginary),
        "imaginary_modes_cm1": list(imaginary),
        "mode_count": len(frequencies),
        "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
        "threshold_source": "tsmode.validation.validate_ts_frequencies (fixed zero crossing)",
    }
    if validation.classification == "first_order_saddle_candidate":
        return _supported(claim_id, statement, validator_id, details=details)
    if validation.classification == "higher_order_saddle":
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                f"{len(imaginary)} imaginary mode(s) were found; a first-order saddle "
                "candidate requires exactly one.",
            ),
        )
    if validation.classification == "no_imaginary":
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=("no imaginary mode was found; the structure is not a saddle candidate.",),
        )
    return _inconclusive(
        claim_id,
        statement,
        validator_id,
        "frequency_not_computed",
        "the frequency list could not be classified",
        details=details,
    )


def validate_ts_mapped_mode(
    frequency_result: Any | None,
    mapped_mode: Any | None,
) -> ClaimAssessment:
    """Decide whether the claimed imaginary mode is identified by native index.

    The selection must name ORCA's native printed mode index (the tsmode
    contract's ``source_mode_index`` is accepted as the same identity) and that
    index must be present in the frequency result's native-mode map and carry a
    negative frequency.  A selection that only points at an array position is
    rejected structurally.  An unresolved mapping status is a contradiction:
    the evidence does not bind the named mode, so the claim is refused rather
    than downgraded to "supported by position".

    Args:
        frequency_result: The ``frequency`` :class:`TaskResult`, or ``None``.
        mapped_mode: :class:`MappedModeSelection` or an equivalent record.

    Returns:
        A four-valued :class:`ClaimAssessment`.
    """
    claim_id = "c-ts-mapped-imaginary-mode"
    statement = (
        "The candidate's imaginary mode is identified by ORCA's native printed "
        "mode index and provenance, not by an array position."
    )
    validator_id = TS_MODE_MAPPING_VALIDATOR_ID

    selection = _coerce_mapped_mode(mapped_mode)
    if frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no frequency result was supplied",
        )
    task = _task_name(frequency_result)
    if task != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"result task is {task!r}, not 'frequency'",
            details={"task": task},
        )
    if selection is None:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "imaginary_mode_missing",
            "no native printed mode index or mode provenance was supplied",
        )

    mode_frequencies = _mode_frequency_map(frequency_result)
    details: dict[str, Any] = {
        "selection_basis": "native_printed_index",
        "native_printed_index": selection.native_printed_index,
        "mapping_status": selection.mapping_status,
        "target_mode_id": selection.target_mode_id,
        "source_step": selection.source_step,
        "source_artifact_digest": selection.source_artifact_digest,
        "native_mode_indices": sorted(mode_frequencies) if mode_frequencies else None,
    }
    if selection.mapping_status != "resolved":
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                f"mapping_status is {selection.mapping_status!r}; only a resolved mapping "
                "identifies a specific imaginary mode.",
            ),
        )
    if not mode_frequencies:
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "imaginary_mode_missing",
            "the frequency result carries no native-mode index map",
            details=details,
        )

    selected_frequency = mode_frequencies.get(selection.native_printed_index)
    details["selected_mode_frequency_cm1"] = selected_frequency
    vectors = _mode_vector_map(frequency_result) or {}
    details["selected_mode_has_vectors"] = selection.native_printed_index in vectors
    if selected_frequency is None:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                f"native printed mode index {selection.native_printed_index} is absent "
                "from the frequency result.",
            ),
        )
    if selected_frequency >= IMAGINARY_MODE_THRESHOLD_CM1:
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(
                f"native printed mode {selection.native_printed_index} has frequency "
                f"{selected_frequency} cm-1 and is not imaginary.",
            ),
        )
    return _supported(claim_id, statement, validator_id, details=details)


def validate_transition_state(
    frequency_result: Any | None,
    mapped_mode: Any | None = None,
    *,
    optimize_result: Any | None = None,
) -> TransitionStateValidation:
    """Decide whether the evidence supports a first-order saddle *candidate*.

    This composes the existing Hessian-evidence check, the exactly-one-
    imaginary-mode classification and the native-index mapped-mode check.  It
    deliberately makes no connectivity claim.  When an optimization result is
    supplied, its convergence is recorded as a prerequisite: a non-converged
    optimization leaves the candidate ``not_evaluated``/``inconclusive`` — it
    never refutes the frequency observation and never claims anything about
    stationary-point existence in the sampled region.

    Args:
        frequency_result: The ``frequency`` :class:`TaskResult`, or ``None``.
        mapped_mode: The selected native printed mode, or ``None``.
        optimize_result: Optional ``optimize`` :class:`TaskResult`.

    Returns:
        A :class:`TransitionStateValidation` whose ``status`` is the composite
        saddle-candidate verdict.
    """
    evidence = validate_frequency_evidence(frequency_result)
    frequency_count = validate_ts_frequency_count(frequency_result)
    mapped = validate_ts_mapped_mode(frequency_result, mapped_mode)
    sub_claims = (evidence, frequency_count, mapped)

    convergence: ClaimAssessment | None = None
    if optimize_result is not None:
        convergence = validate_optimization_convergence(optimize_result)

    notes: list[str] = [
        "A first-order saddle candidate is local mode evidence at the reported "
        "geometry; it does not prove connectivity and is not evidence that no "
        "other stationary point exists.",
    ]
    if any(claim.status == "refuted" for claim in sub_claims):
        status: ClaimStatus = "refuted"
        reason: AssessmentReason = "contradiction"
        missing_checks: tuple[MissingCheck, ...] = ()
    elif convergence is not None and convergence.status != "supported":
        status = "inconclusive" if convergence.status == "inconclusive" else "not_evaluated"
        reason = "missing_evidence" if status == "inconclusive" else "not_checked"
        missing_checks = convergence.missing_checks or (
            MissingCheck(
                "convergence_not_met",
                "the optimization prerequisite did not establish convergence",
            ),
        )
        notes.append(
            "The optimization prerequisite is not supported; the candidate is "
            "not promoted, and a non-converged optimization is not evidence that "
            "no stationary point exists in the sampled region."
        )
    elif any(claim.status == "inconclusive" for claim in sub_claims):
        status = "inconclusive"
        reason = "missing_evidence"
        missing_checks = _merge_missing_checks(
            [claim for claim in sub_claims if claim.status == "inconclusive"]
        )
    elif any(claim.status == "not_evaluated" for claim in sub_claims):
        status = "not_evaluated"
        reason = "not_checked"
        missing_checks = _merge_missing_checks(
            [claim for claim in sub_claims if claim.status == "not_evaluated"]
        )
    else:
        status = "supported"
        reason = "affirmed"
        missing_checks = ()

    imaginary_modes = tuple(
        float(value) for value in frequency_count.details.get("imaginary_modes_cm1", ())
    )
    mapped_index = mapped.details.get("native_printed_index")
    candidate = ClaimAssessment(
        claim_id="c-ts-first-order-saddle-candidate",
        statement=(
            "The frequency evidence is consistent with a local first-order "
            "saddle candidate at the reported geometry."
        ),
        status=status,
        reason=reason,
        validator_id=TS_SADDLE_CANDIDATE_VALIDATOR_ID,
        validator_version=VALIDATOR_VERSION,
        missing_checks=missing_checks,
        details={
            "classification": frequency_count.details.get("classification"),
            "imaginary_mode_count": frequency_count.details.get("imaginary_mode_count"),
            "mode_count": frequency_count.details.get("mode_count"),
            "imaginary_modes_cm1": list(imaginary_modes),
            "mapped_mode_index": mapped_index,
            "mapped_mode_frequency_cm1": mapped.details.get("selected_mode_frequency_cm1"),
            "convergence_status": None if convergence is None else convergence.status,
            "evidence_scope": "local mode evidence at the reported geometry",
            "connectivity_evaluated": False,
        },
        notes=tuple(notes),
    )

    claims: list[ClaimAssessment] = [evidence, frequency_count, mapped, candidate]
    if convergence is not None:
        claims.insert(0, convergence)
    return TransitionStateValidation(
        claims=tuple(claims),
        classification=frequency_count.details.get("classification"),
        imaginary_mode_count=frequency_count.details.get("imaginary_mode_count"),
        mode_count=frequency_count.details.get("mode_count"),
        imaginary_modes_cm1=imaginary_modes,
        mapped_mode_index=mapped_index if isinstance(mapped_index, int) else None,
    )


def validate_ts_connectivity(
    transition_state: TransitionStateValidation | None,
    *,
    irc_result: Any | None = None,
) -> ClaimAssessment:
    """Decide whether the transition state connects the intended endpoints.

    Mode evidence never proves connectivity: one imaginary mode plus a mapped
    mode identifies a local saddle candidate only.  Without bidirectional IRC
    evidence the claim is ``inconclusive`` with the frozen ``missing_irc``
    check.  IRC endpoint validation itself is delivered by A-16b, so a supplied
    IRC result is recorded as ``not_evaluated`` rather than misread here.

    Args:
        transition_state: Optional composite saddle-candidate validation.
        irc_result: Optional IRC result reserved for the A-16b validator.

    Returns:
        A four-valued :class:`ClaimAssessment` that is never ``supported``.
    """
    claim_id = "c-ts-connects-intended-endpoints"
    statement = (
        "The transition state connects the intended reactant and product "
        "(proven connectivity, not a local mode observation)."
    )
    validator_id = TS_CONNECTIVITY_VALIDATOR_ID
    details: dict[str, Any] = {
        "ts_candidate_status": None if transition_state is None else transition_state.status,
        "ts_candidate_claim_id": (
            None if transition_state is None else transition_state.candidate.claim_id
        ),
        "imaginary_mode_count": (
            None if transition_state is None else transition_state.imaginary_mode_count
        ),
        "mapped_mode_index": (
            None if transition_state is None else transition_state.mapped_mode_index
        ),
    }
    if irc_result is not None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "validator_unavailable",
            "IRC endpoint connectivity validation is not part of the A-16a validator",
            details=details,
        )
    return _inconclusive(
        claim_id,
        statement,
        validator_id,
        "missing_irc",
        "one imaginary mode plus a mapped mode identifies a local saddle candidate "
        "but never proves which reactant and product it connects",
        details=details,
        notes=(
            "One imaginary mode never proves connectivity: bidirectional IRC "
            "endpoint mapping is required (A-16b).",
        ),
    )


def validate_no_stationary_point_exists(optimize_result: Any | None) -> ClaimAssessment:
    """Evaluate the strong proposition "no stationary point exists in the region".

    A failed optimization is evidence about that optimization, not about the
    existence of stationary points.  The only way this validator refutes the
    absence proposition is a *converged* optimization (which exhibits a
    stationary geometry); a non-converged run is ``not_evaluated`` with
    ``convergence_not_met``.  The absence proposition is therefore never
    ``supported`` by a single optimization result.

    Args:
        optimize_result: The ``optimize`` :class:`TaskResult`, or ``None``.

    Returns:
        A four-valued :class:`ClaimAssessment`.
    """
    claim_id = "c-no-stationary-point-in-sampled-region"
    statement = "No stationary point exists in the sampled region."
    validator_id = STATIONARY_POINT_EXISTENCE_VALIDATOR_ID

    if optimize_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no optimization result was supplied",
        )
    task = _task_name(optimize_result)
    if task != "optimize":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            f"result task is {task!r}, not 'optimize'",
            details={"task": task},
        )

    convergence = validate_optimization_convergence(optimize_result)
    details: dict[str, Any] = {
        "underlying_convergence_status": convergence.status,
        "underlying_claim_id": convergence.claim_id,
        "claim_scope": "sampled region reached by this optimization only",
        "note": "a single optimization cannot establish the absence of stationary points",
    }
    if convergence.status == "supported":
        return _refuted(claim_id, statement, validator_id, details=details)
    if convergence.status == "refuted":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "convergence_not_met",
            "the optimization failed to converge; this is not evidence that no "
            "stationary point exists in the sampled region",
            details=details,
            notes=(
                "A non-converged optimization refutes only that the optimization "
                "converged; the absence proposition remains unevaluated.",
            ),
        )
    if convergence.status == "inconclusive":
        return _inconclusive(
            claim_id,
            statement,
            validator_id,
            "convergence_not_met",
            "the optimization did not establish convergence; absence of a stationary "
            "point cannot be concluded",
            details=details,
            notes=("Missing convergence evidence is not a refutation of the absence claim.",),
        )
    return _not_evaluated(
        claim_id,
        statement,
        validator_id,
        "not_executed",
        "the optimization convergence check was not evaluated",
        details=details,
    )


def validate_stationary_point(
    optimize_result: Any | None,
    frequency_result: Any | None,
) -> StationaryPointValidation:
    """Decide whether the optimized geometry is a true minimum.

    Convergence is checked first.  Any non-supporting convergence verdict
    short-circuits the composite claim, so an unconverged or unevaluated
    optimization can never be reported as a minimum.  With convergence
    supported, the composite claim additionally requires structure membership,
    Hessian evidence, method consistency, and zero imaginary modes.  An
    electronic state that is declared on neither side is recorded as a
    limitation rather than fabricated as a passed check.

    Args:
        optimize_result: The ``optimize`` :class:`TaskResult`, or ``None``.
        frequency_result: The ``frequency`` :class:`TaskResult`, or ``None``.

    Returns:
        A :class:`StationaryPointValidation` whose ``status`` is the frozen
        four-valued verdict.
    """
    convergence = validate_optimization_convergence(optimize_result)
    if convergence.status != "supported":
        minimum = _stationary_minimum(
            convergence.status,
            convergence.reason,
            convergence.missing_checks,
            details={
                "blocked_by": convergence.claim_id,
                "convergence_status": convergence.status,
            },
            notes=("An optimization that has not converged can never be reported as a minimum.",),
        )
        return StationaryPointValidation(claims=(convergence, minimum))

    membership = validate_frequency_membership(optimize_result, frequency_result)
    evidence = validate_frequency_evidence(frequency_result)
    consistency = validate_method_state_consistency(optimize_result, frequency_result)
    imaginary = _validate_imaginary_modes(frequency_result)
    frequencies = _frequency_list(frequency_result) or ()
    soft_modes = tuple(
        sorted(
            value
            for value in frequencies
            if IMAGINARY_MODE_THRESHOLD_CM1 <= value < LOW_FREQUENCY_THRESHOLD_CM1
        )
    )

    state_not_declared = (
        consistency.electronic_state.status == "not_evaluated"
        and consistency.electronic_state.details.get("state_declared_on") == "neither"
    )
    sub_claims = (
        membership,
        evidence,
        consistency.method,
        consistency.electronic_state,
        imaginary,
    )
    blocking = [
        claim
        for claim in sub_claims
        if claim.status in ("inconclusive", "not_evaluated")
        and not (claim is consistency.electronic_state and state_not_declared)
    ]

    notes: list[str] = []
    if state_not_declared:
        notes.append(
            "No electronic-state identity was declared on either side; state equality is "
            "not verified and the claim is limited to structure and method."
        )
    if soft_modes:
        notes.append(
            f"{len(soft_modes)} real mode(s) below the frozen "
            f"{LOW_FREQUENCY_THRESHOLD_CM1} cm-1 soft-mode threshold; the minimum may be "
            "shallow but a low real frequency is not a refutation."
        )

    if any(claim.status == "refuted" for claim in sub_claims):
        status: ClaimStatus = "refuted"
        reason: AssessmentReason = "contradiction"
    elif any(claim.status == "inconclusive" for claim in blocking):
        status = "inconclusive"
        reason = "missing_evidence"
    elif blocking:
        status = "not_evaluated"
        reason = "not_checked"
    else:
        status = "supported"
        reason = "affirmed"

    missing_checks = _merge_missing_checks(blocking)
    imaginary_details = imaginary.details
    mode_count = imaginary_details.get("mode_count")
    imaginary_count = imaginary_details.get("imaginary_mode_count")
    minimum = _stationary_minimum(
        status,
        reason,
        missing_checks,
        details={
            "sub_claim_status": {
                "membership": membership.status,
                "frequency_evidence": evidence.status,
                "method": consistency.method.status,
                "electronic_state": consistency.electronic_state.status,
                "imaginary_modes": imaginary.status,
            },
            "imaginary_mode_count": imaginary_count,
            "mode_count": mode_count,
            "soft_modes_cm1": list(soft_modes),
            "low_frequency_threshold_cm1": LOW_FREQUENCY_THRESHOLD_CM1,
            "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
            "coordinate_abs_tolerance_angstrom": COORDINATE_ABS_TOLERANCE_ANGSTROM,
            "optimized_structure_digest": membership.details.get("optimized_structure_digest"),
            "frequency_structure_digest": membership.details.get("frequency_structure_digest"),
        },
        notes=tuple(notes),
    )
    return StationaryPointValidation(
        claims=(
            convergence,
            membership,
            evidence,
            consistency.method,
            consistency.electronic_state,
            imaginary,
            minimum,
        ),
        imaginary_mode_count=imaginary_count if isinstance(imaginary_count, int) else None,
        mode_count=mode_count if isinstance(mode_count, int) else None,
        soft_modes_cm1=soft_modes,
        optimized_structure_digest=_optional_str(
            membership.details.get("optimized_structure_digest")
        ),
        frequency_structure_digest=_optional_str(
            membership.details.get("frequency_structure_digest")
        ),
    )


# ── composite helpers ──────────────────────────────────────────────────────


def _validate_imaginary_modes(frequency_result: Any | None) -> ClaimAssessment:
    claim_id = "c-no-imaginary-modes"
    statement = "The frequency result contains no imaginary modes."
    validator_id = STATIONARY_POINT_VALIDATOR_ID

    if frequency_result is None:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "no frequency result was supplied",
        )
    if _task_name(frequency_result) != "frequency":
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "not_executed",
            "imaginary-mode classification requires a frequency result",
        )
    frequencies = _frequency_list(frequency_result)
    if not frequencies:
        return _not_evaluated(
            claim_id,
            statement,
            validator_id,
            "frequency_not_computed",
            "the frequency result carries no vibrational frequencies",
        )

    validation = _imaginary_classification(frequencies)
    details: dict[str, Any] = {
        "classification": validation.classification,
        "imaginary_mode_count": len(validation.all_imaginary_cm1),
        "imaginary_modes_cm1": list(validation.all_imaginary_cm1),
        "mode_count": len(frequencies),
        "imaginary_mode_threshold_cm1": IMAGINARY_MODE_THRESHOLD_CM1,
        "threshold_source": "tsmode.validation.validate_ts_frequencies (fixed zero crossing)",
    }
    if validation.classification == "no_imaginary":
        return _supported(claim_id, statement, validator_id, details=details)
    if validation.classification in ("first_order_saddle_candidate", "higher_order_saddle"):
        return _refuted(
            claim_id,
            statement,
            validator_id,
            details=details,
            notes=(f"classification={validation.classification}",),
        )
    return _inconclusive(
        claim_id,
        statement,
        validator_id,
        "frequency_not_computed",
        "the frequency list could not be classified",
        details=details,
    )


def _stationary_minimum(
    status: ClaimStatus,
    reason: AssessmentReason,
    missing_checks: tuple[MissingCheck, ...],
    *,
    details: dict[str, Any],
    notes: tuple[str, ...],
) -> ClaimAssessment:
    if status in ("inconclusive", "not_evaluated") and not missing_checks:
        missing_checks = (
            MissingCheck("not_executed", "the stationary-point check was not completed"),
        )
    return ClaimAssessment(
        claim_id="c-stationary-point-minimum",
        statement="The optimized geometry is a true minimum on the potential energy surface.",
        status=status,
        reason=reason,
        validator_id=STATIONARY_POINT_VALIDATOR_ID,
        validator_version=VALIDATOR_VERSION,
        missing_checks=missing_checks,
        details=details,
        notes=notes,
    )


def _merge_missing_checks(claims: list[ClaimAssessment]) -> tuple[MissingCheck, ...]:
    merged: dict[tuple[str, str], MissingCheck] = {}
    for claim in claims:
        for check in claim.missing_checks:
            merged[(check.check_id, check.reason)] = check
    return tuple(merged.values())


def _state_declaration_scope(
    optimize_state: dict[str, Any] | None,
    frequency_state: dict[str, Any] | None,
) -> str:
    if optimize_state is not None and frequency_state is not None:
        return "both"
    if optimize_state is not None:
        return "optimize"
    if frequency_state is not None:
        return "frequency"
    return "neither"


def _max_component_delta(
    left: tuple[tuple[float, ...], ...],
    right: tuple[tuple[float, ...], ...],
) -> float | None:
    if len(left) != len(right):
        return None
    worst = 0.0
    for left_row, right_row in zip(left, right):
        if len(left_row) != len(right_row):
            return None
        for left_value, right_value in zip(left_row, right_row):
            worst = max(worst, abs(left_value - right_value))
    return worst


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
