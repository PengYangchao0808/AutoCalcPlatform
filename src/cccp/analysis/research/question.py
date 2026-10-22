"""Public ACP2 scientific question type (``question.schema.json``, major v1).

The frozen JSON contract in
``docs/reference/next-change/acp2-contracts/science/question.schema.json``
declares a limited research question: its type, hypothesis references, the
evidence kinds it needs, the frozen four-valued claim vocabulary and the
comparison envelope that must be compatible before any claim is evaluated.

This module provides the immutable Python mirror: frozen dataclasses with
explicit ``to_dict``/``from_dict``, strict schema-version checks and a
historical read adapter.  Two boundaries are deliberate:

* **Pure types, no execution.** Importing this module must never import
  ``cccp.calculation.tasks``, ``cccp.qc`` or ``acp``.  The existing
  electronic-state model is imported lazily only when a caller explicitly
  binds a declaration to it; nothing is guessed at import time.
* **No platform/model envelope.** There is deliberately no field for an LLM
  role, cost/price/budget, queue/account or platform job status.  A payload
  carrying any of those names is rejected rather than silently ignored.

The canonical execution model remains
:class:`cccp.calculation.contracts.ElectronicStateSpec`; this contract carries
only the comparability reference (charge, multiplicity and an optional
``state_id``) and never redefines spin handling.

Author: QCcalc Team
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal

from cccp.analysis.research._common import (
    ForbiddenPlatformFieldError,
    ScienceContractError,
    canonical_json,
    read_schema_version,
    reject_forbidden_keys,
    reject_unknown_keys,
    require_int,
    require_keys,
    require_mapping,
    require_number,
    require_sequence,
    require_string,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from cccp.calculation.contracts import ElectronicStateSpec

logger = logging.getLogger(__name__)

__all__ = [
    "CLAIM_STATUSES",
    "COMPATIBILITY_REQUIREMENTS",
    "ENERGY_UNITS",
    "EVIDENCE_TYPES",
    "QUESTION_TYPES",
    "SCHEMA_VERSION",
    "STANDARD_STATES",
    "ComparisonConditions",
    "ElectronicStateRef",
    "HistoricalQuestionRecord",
    "Question",
    "QuestionConditionError",
    "QuestionForbiddenFieldError",
    "QuestionSchemaError",
    "QuestionTypeError",
    "UnknownQuestionDeclarationError",
    "UnsupportedQuestionSchemaVersionError",
    "canonical_json",
    "read_historical_question",
]

SCHEMA_VERSION: Final = 1
QUESTION_TYPES: Final = frozenset({"path_connectivity", "activation_energy"})
CLAIM_STATUSES: Final = ("supported", "refuted", "inconclusive", "not_evaluated")
EVIDENCE_TYPES: Final = frozenset(
    {
        "transition_state_geometry",
        "imaginary_frequency",
        "irc_forward",
        "irc_reverse",
        "stationary_point_gradient",
        "path_endpoints",
        "energy_difference",
        "thermochemistry",
    }
)
COMPATIBILITY_REQUIREMENTS: Final = frozenset(
    {
        "same_method",
        "same_electronic_state",
        "same_temperature",
        "same_pressure",
        "same_standard_state",
        "same_reference_state",
        "same_energy_unit",
    }
)
ENERGY_UNITS: Final = frozenset({"hartree", "kcal_per_mol", "kj_per_mol"})
STANDARD_STATES: Final = frozenset(
    {"1_bar_ideal_gas", "1_molar_ideal_solution", "unknown"}
)

_QUESTION_KEYS: Final = frozenset(
    {
        "schema_version",
        "question_id",
        "question_type",
        "hypothesis_refs",
        "required_evidence_types",
        "claim_status_vocabulary",
        "comparison_conditions",
    }
)
_COMPARISON_KEYS: Final = frozenset(
    {
        "method",
        "electronic_state",
        "temperature_k",
        "pressure_pa",
        "standard_state",
        "reference_state",
        "energy_unit",
        "compatibility_requirements",
        "unknown_fields",
    }
)
_ELECTRONIC_STATE_KEYS: Final = frozenset({"charge", "multiplicity", "state_id"})

_NULLABLE_CONDITION_PATHS: Final = (
    "temperature_k",
    "pressure_pa",
    "standard_state",
    "reference_state",
)
_MISSING: Final = object()


# ── typed errors ────────────────────────────────────────────────────────────


class QuestionTypeError(ScienceContractError):
    """Base class for a rejected scientific-question payload."""


class UnsupportedQuestionSchemaVersionError(QuestionTypeError):
    """The payload declares a question contract major version other than 1."""


class QuestionSchemaError(QuestionTypeError):
    """A question payload does not have the frozen v1 structure."""


class QuestionForbiddenFieldError(QuestionTypeError):
    """A question payload carries a forbidden platform/model field."""


class QuestionConditionError(QuestionTypeError):
    """A comparison condition is malformed or not comparable."""


class UnknownQuestionDeclarationError(QuestionTypeError):
    """A declared-unknown condition is missing, defaulted or inconsistent."""


# ── helpers ─────────────────────────────────────────────────────────────────


def _unique_strings(
    value: Any, where: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    items = require_sequence(value, where)
    if not items and not allow_empty:
        raise QuestionSchemaError(f"{where}: must not be empty")
    result = tuple(
        require_string(item, f"{where}[{index}]") for index, item in enumerate(items)
    )
    if len(set(result)) != len(result):
        raise QuestionSchemaError(f"{where}: duplicate entries {sorted(result)}")
    return result


def _optional_number(value: Any, where: str) -> int | float | None:
    if value is None:
        return None
    return require_number(value, where, minimum=0.0)


def _optional_string(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return require_string(value, where)


def _resolve_path(document: Mapping[str, Any], dotted: str) -> Any:
    current: Any = document
    for part in dotted.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


# ── electronic-state reference ──────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ElectronicStateRef:
    """Comparability reference for one electronic state.

    This is not a second spin model: charge and multiplicity are the physical
    state of the compared results, ``state_id`` optionally names an existing
    :class:`cccp.calculation.contracts.ElectronicStateSpec` state and ``None``
    means explicitly unnamed (never a guessed state).
    """

    charge: int
    multiplicity: int
    state_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "charge", require_int(self.charge, "electronic_state.charge"))
        object.__setattr__(
            self,
            "multiplicity",
            require_int(
                self.multiplicity, "electronic_state.multiplicity", minimum=1
            ),
        )
        if self.state_id is not None:
            require_string(self.state_id, "electronic_state.state_id")

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped mapping (``state_id`` stays explicit)."""
        return {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "state_id": self.state_id,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "electronic_state"
    ) -> ElectronicStateRef:
        """Build an electronic-state reference from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_unknown_keys(data, _ELECTRONIC_STATE_KEYS, where)
        require_keys(data, {"charge", "multiplicity"}, where)
        return cls(
            charge=require_int(data["charge"], f"{where}.charge"),
            multiplicity=require_int(
                data["multiplicity"], f"{where}.multiplicity", minimum=1
            ),
            state_id=(
                None
                if data.get("state_id") is None
                else require_string(data["state_id"], f"{where}.state_id")
            ),
        )

    def to_electronic_state_spec(self) -> ElectronicStateSpec:
        """Bind this reference to the existing execution-layer state model.

        ``ElectronicStateSpec`` remains the single definition of spin handling;
        this method only reuses its ``state_id`` and ``target_multiplicity``.
        Charge remains a property of the calculation request, not of the spec.
        """
        if not self.state_id:
            raise QuestionConditionError(
                "electronic_state.state_id is required to bind an ElectronicStateSpec; "
                "an unnamed state is never guessed"
            )
        from cccp.calculation.contracts import ElectronicStateSpec

        return ElectronicStateSpec(
            state_id=self.state_id,
            target_multiplicity=self.multiplicity,
        )


# ── comparison conditions ───────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ComparisonConditions:
    """Scientific comparability envelope declared by a question.

    A null condition is only legal when its dotted path is listed in
    :attr:`unknown_fields`; absence is never treated as a silent match.
    """

    method: str
    electronic_state: ElectronicStateRef
    temperature_k: int | float | None
    pressure_pa: int | float | None
    standard_state: str | None
    reference_state: str | None
    energy_unit: Literal["hartree", "kcal_per_mol", "kj_per_mol"]
    compatibility_requirements: tuple[str, ...]
    unknown_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_string(self.method, "comparison_conditions.method")
        state = (
            self.electronic_state
            if isinstance(self.electronic_state, ElectronicStateRef)
            else ElectronicStateRef.from_dict(
                require_mapping(self.electronic_state, "comparison_conditions.electronic_state"),
                "comparison_conditions.electronic_state",
            )
        )
        object.__setattr__(self, "electronic_state", state)
        object.__setattr__(
            self,
            "temperature_k",
            _optional_number(self.temperature_k, "comparison_conditions.temperature_k"),
        )
        object.__setattr__(
            self,
            "pressure_pa",
            _optional_number(self.pressure_pa, "comparison_conditions.pressure_pa"),
        )
        if self.standard_state is not None:
            require_string(self.standard_state, "comparison_conditions.standard_state")
            if self.standard_state not in STANDARD_STATES:
                raise QuestionSchemaError(
                    "comparison_conditions.standard_state must be one of "
                    f"{sorted(STANDARD_STATES)} or null, got {self.standard_state!r}"
                )
        if self.reference_state is not None:
            require_string(self.reference_state, "comparison_conditions.reference_state")
        if self.energy_unit not in ENERGY_UNITS:
            raise QuestionSchemaError(
                f"comparison_conditions.energy_unit must be one of "
                f"{sorted(ENERGY_UNITS)}, got {self.energy_unit!r}"
            )
        requirements = _unique_strings(
            self.compatibility_requirements,
            "comparison_conditions.compatibility_requirements",
        )
        unknown = _unique_strings(
            self.unknown_fields,
            "comparison_conditions.unknown_fields",
            allow_empty=True,
        )
        object.__setattr__(self, "compatibility_requirements", requirements)
        object.__setattr__(self, "unknown_fields", unknown)

        unsupported = sorted(set(requirements) - COMPATIBILITY_REQUIREMENTS)
        if unsupported:
            raise QuestionSchemaError(
                "comparison_conditions.compatibility_requirements has unsupported "
                f"entries {unsupported}"
            )

        view = {
            "method": self.method,
            "electronic_state": state.to_dict(),
            "temperature_k": self.temperature_k,
            "pressure_pa": self.pressure_pa,
            "standard_state": self.standard_state,
            "reference_state": self.reference_state,
            "energy_unit": self.energy_unit,
            "compatibility_requirements": list(requirements),
            "unknown_fields": list(unknown),
        }
        for dotted in unknown:
            value = _resolve_path(view, dotted)
            if value is _MISSING:
                raise UnknownQuestionDeclarationError(
                    f"comparison_conditions.unknown_fields names {dotted!r}, "
                    "which does not exist"
                )
            if value is not None:
                raise UnknownQuestionDeclarationError(
                    f"comparison_conditions.unknown_fields names {dotted!r} but its "
                    f"value is {value!r}, not unknown"
                )
        for path in _NULLABLE_CONDITION_PATHS:
            if getattr(self, path) is None and path not in unknown:
                raise UnknownQuestionDeclarationError(
                    f"comparison_conditions.{path} is null but is not declared in "
                    "unknown_fields; a missing condition is never defaulted"
                )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped comparability envelope."""
        return {
            "method": self.method,
            "electronic_state": self.electronic_state.to_dict(),
            "temperature_k": self.temperature_k,
            "pressure_pa": self.pressure_pa,
            "standard_state": self.standard_state,
            "reference_state": self.reference_state,
            "energy_unit": self.energy_unit,
            "compatibility_requirements": list(self.compatibility_requirements),
            "unknown_fields": list(self.unknown_fields),
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "comparison_conditions"
    ) -> ComparisonConditions:
        """Build a comparability envelope from its canonical mapping."""
        data = require_mapping(payload, where)
        reject_unknown_keys(data, _COMPARISON_KEYS, where)
        require_keys(data, _COMPARISON_KEYS, where)
        return cls(
            method=require_string(data["method"], f"{where}.method"),
            electronic_state=ElectronicStateRef.from_dict(
                require_mapping(data["electronic_state"], f"{where}.electronic_state"),
                f"{where}.electronic_state",
            ),
            temperature_k=_optional_number(
                data["temperature_k"], f"{where}.temperature_k"
            ),
            pressure_pa=_optional_number(data["pressure_pa"], f"{where}.pressure_pa"),
            standard_state=(
                None
                if data["standard_state"] is None
                else require_string(data["standard_state"], f"{where}.standard_state")
            ),
            reference_state=(
                None
                if data["reference_state"] is None
                else require_string(data["reference_state"], f"{where}.reference_state")
            ),
            energy_unit=require_string(data["energy_unit"], f"{where}.energy_unit"),
            compatibility_requirements=_unique_strings(
                data["compatibility_requirements"],
                f"{where}.compatibility_requirements",
            ),
            unknown_fields=_unique_strings(
                data["unknown_fields"], f"{where}.unknown_fields", allow_empty=True
            ),
        )


# ── question ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Question:
    """Frozen ACP2 scientific question (``question.schema.json`` v1)."""

    question_id: str
    question_type: Literal["path_connectivity", "activation_energy"]
    hypothesis_refs: tuple[str, ...]
    required_evidence_types: tuple[str, ...]
    comparison_conditions: ComparisonConditions
    claim_status_vocabulary: tuple[str, ...] = CLAIM_STATUSES
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if isinstance(self.schema_version, bool) or self.schema_version != SCHEMA_VERSION:
            raise UnsupportedQuestionSchemaVersionError(
                f"unsupported question schema_version {self.schema_version!r}; "
                f"only {SCHEMA_VERSION} is readable as a canonical question"
            )
        require_string(self.question_id, "question.question_id")
        if self.question_type not in QUESTION_TYPES:
            raise QuestionSchemaError(
                f"question.question_type must be one of {sorted(QUESTION_TYPES)}, "
                f"got {self.question_type!r}"
            )
        refs = _unique_strings(self.hypothesis_refs, "question.hypothesis_refs")
        evidence = _unique_strings(
            self.required_evidence_types, "question.required_evidence_types"
        )
        unsupported = sorted(set(evidence) - EVIDENCE_TYPES)
        if unsupported:
            raise QuestionSchemaError(
                f"question.required_evidence_types has unsupported entries {unsupported}"
            )
        vocabulary = _unique_strings(
            self.claim_status_vocabulary, "question.claim_status_vocabulary"
        )
        if vocabulary != CLAIM_STATUSES:
            raise QuestionSchemaError(
                "question.claim_status_vocabulary must be exactly "
                f"{list(CLAIM_STATUSES)}, got {list(vocabulary)}"
            )
        conditions = (
            self.comparison_conditions
            if isinstance(self.comparison_conditions, ComparisonConditions)
            else ComparisonConditions.from_dict(
                require_mapping(
                    self.comparison_conditions, "question.comparison_conditions"
                ),
                "question.comparison_conditions",
            )
        )
        object.__setattr__(self, "hypothesis_refs", refs)
        object.__setattr__(self, "required_evidence_types", evidence)
        object.__setattr__(self, "claim_status_vocabulary", vocabulary)
        object.__setattr__(self, "comparison_conditions", conditions)

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical schema-shaped mapping for this question."""
        return {
            "schema_version": self.schema_version,
            "question_id": self.question_id,
            "question_type": self.question_type,
            "hypothesis_refs": list(self.hypothesis_refs),
            "required_evidence_types": list(self.required_evidence_types),
            "claim_status_vocabulary": list(self.claim_status_vocabulary),
            "comparison_conditions": self.comparison_conditions.to_dict(),
        }

    def to_json(self) -> str:
        """Return the canonical JSON text (sorted keys, stable whitespace)."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Question:
        """Build a question from canonical v1 JSON, rejecting unknown majors."""
        data = require_mapping(payload, "question")
        try:
            reject_forbidden_keys(data, "question")
        except ForbiddenPlatformFieldError as exc:
            raise QuestionForbiddenFieldError(str(exc)) from exc
        version = read_schema_version(data, "question")
        if version != SCHEMA_VERSION:
            raise UnsupportedQuestionSchemaVersionError(
                f"unsupported question schema_version {version!r}; only "
                f"{SCHEMA_VERSION} is readable as a canonical question "
                "(unknown majors are never coerced)"
            )
        reject_unknown_keys(data, _QUESTION_KEYS, "question")
        require_keys(data, _QUESTION_KEYS, "question")
        return cls(
            schema_version=version,
            question_id=require_string(data["question_id"], "question.question_id"),
            question_type=require_string(
                data["question_type"], "question.question_type"
            ),
            hypothesis_refs=_unique_strings(
                data["hypothesis_refs"], "question.hypothesis_refs"
            ),
            required_evidence_types=_unique_strings(
                data["required_evidence_types"], "question.required_evidence_types"
            ),
            comparison_conditions=ComparisonConditions.from_dict(
                require_mapping(
                    data["comparison_conditions"], "question.comparison_conditions"
                ),
                "question.comparison_conditions",
            ),
            claim_status_vocabulary=_unique_strings(
                data["claim_status_vocabulary"], "question.claim_status_vocabulary"
            ),
        )


# ── historical compatibility ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class HistoricalQuestionRecord:
    """A readable historical question payload with no execution eligibility.

    Unknown fields are preserved verbatim in :attr:`payload`; nothing is
    defaulted or upgraded.  Tolerating them never grants mutation, execution or
    recovery eligibility.
    """

    payload: dict[str, Any]
    schema_version: int | None
    adapter: str = "historical_question_v1"
    execution_eligible: bool = False
    recovery_eligible: bool = False

    @property
    def unknown_fields(self) -> tuple[str, ...]:
        """Top-level fields not declared by the canonical v1 question contract."""
        return tuple(sorted(set(self.payload) - _QUESTION_KEYS))

    def to_dict(self) -> dict[str, Any]:
        """Return a deep copy of the preserved payload (byte-stable re-read)."""
        return copy.deepcopy(self.payload)


def read_historical_question(
    payload: Mapping[str, Any], *, schema_version: int | None = None
) -> HistoricalQuestionRecord:
    """Read a historical question payload without canonical coercion.

    Unknown (non-forbidden) fields stay in the returned record instead of being
    dropped or filled with defaults.  The record is explicitly not eligible for
    execution or recovery; use :meth:`Question.from_dict` for new work.
    """
    data = require_mapping(payload, "historical_question")
    try:
        reject_forbidden_keys(data, "historical_question")
    except ForbiddenPlatformFieldError as exc:
        raise QuestionForbiddenFieldError(str(exc)) from exc
    declared = schema_version
    if declared is None and "schema_version" in data:
        raw = data["schema_version"]
        if isinstance(raw, int) and not isinstance(raw, bool):
            declared = raw
    return HistoricalQuestionRecord(
        payload=copy.deepcopy(data),
        schema_version=declared,
        execution_eligible=False,
        recovery_eligible=False,
    )
