"""Frozen ACP2 scientific recipe contract (``recipe.schema.json`` v1).

A recipe is a finite scientific DAG: stable step ids, a reference to a
registered capability *or* an existing :data:`TaskKind <cccp.calculation.
requests.TaskKind>` from the fifteen-kind inventory, typed
:class:`OutputRef` selectors for upstream products, edges, required and
diagnostic-only flags, validators, applicability and bounded alternatives.

Two boundaries are deliberate:

* **Science only.** No job, queue, account, price or execution status field
  exists at any depth, and there is no scheduler, dispatch loop or second
  executor.  Platform state belongs to Runtime and is never restated here.
* **Pure types.** Module-level imports are standard-library only (plus the
  frozen canonical-JSON helper); task modules are never imported.  The
  fifteen-kind inventory is read lazily from
  :mod:`cccp.calculation.capabilities.task_binding`, which owns the single
  authoritative list.

Canonical serialization is ``to_dict``/``from_dict`` with an explicit
schema-version check; :meth:`Recipe.to_json` uses the frozen encoding shared
with the other ACP2 science types.

Author: QCcalc Team
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from types import MappingProxyType
from typing import Any, Final

from cccp.analysis.research._common import canonical_json
from cccp.calculation.errors import CalculationError

logger = logging.getLogger(__name__)

__all__ = [
    "RECIPE_SCHEMA",
    "RECIPE_SCHEMA_VERSION",
    "SAFE_FIELD_PATTERN",
    "SELECTOR_KINDS",
    "STEP_ID_PATTERN",
    "Alternative",
    "Applicability",
    "Edge",
    "Limits",
    "OutputRef",
    "Recipe",
    "RecipeContractError",
    "RecipeForbiddenFieldError",
    "RecipeSchemaError",
    "RecipeSchemaVersionError",
    "Selector",
    "Step",
    "StepFlagConflictError",
    "UnknownRecipeTaskKindError",
    "ValidatorRef",
    "existing_task_kinds",
]

#: Contract identity frozen by the ACP2 science recipe schema.
RECIPE_SCHEMA = "acp2.science.recipe"
RECIPE_SCHEMA_VERSION = 1
STEP_ID_PATTERN = r"^[a-z0-9][a-z0-9_.-]*$"
SAFE_FIELD_PATTERN = r"^[a-z][a-z0-9_]*$"

#: The seven typed selector kinds declared by the frozen schema.
SELECTOR_KINDS: Final[tuple[str, ...]] = (
    "whole_product",
    "index",
    "slice",
    "field",
    "conformer",
    "mode",
    "frame",
)

_STEP_ID_RE = re.compile(STEP_ID_PATTERN)
_SAFE_FIELD_RE = re.compile(SAFE_FIELD_PATTERN)
_PLATFORM_TOKEN_RE = re.compile(r"(job|queue|account|price|status)")
_SCIENCE_IDENTITY_RE = re.compile(r"^v2:[0-9a-f]{32}$")
_VALIDATOR_ID_RE = re.compile(r"^(?:check|software|human)\.[a-z0-9][a-z0-9_.-]*$")
_VALIDATOR_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")

_SCALAR_TYPES = (str, int, float, bool)


# ---------------------------------------------------------------------------
# Typed contract errors
# ---------------------------------------------------------------------------


class RecipeContractError(CalculationError, ValueError):
    """Base class for a rejected ACP2 recipe payload.

    The record carries the frozen ``errors.schema.json`` fields so a caller
    can surface ``code``/``layer``/``detail`` without parsing messages.
    """

    code = "invalid_scientific_input"
    layer = "scientific"
    retryable = False

    def __init__(self, message: str, *, detail: Mapping[str, object] | None = None) -> None:
        """Create the error with structured detail.

        Args:
            message: Human-readable message.
            detail: Structured detail required by the frozen error schema.
        """
        super().__init__(message)
        self.detail = dict(detail or {})

    def to_error_record(self) -> dict[str, object]:
        """Return the schema-shaped error record."""
        return {
            "code": self.code,
            "layer": self.layer,
            "retryable": self.retryable,
            "detail": dict(self.detail),
            "detail_required": True,
            "message": str(self),
        }


class RecipeSchemaVersionError(RecipeContractError):
    """The payload declares a recipe major version other than 1."""


class RecipeSchemaError(RecipeContractError):
    """A recipe payload does not have the frozen v1 structure."""


class RecipeForbiddenFieldError(RecipeContractError):
    """A recipe field name carries a forbidden platform/science-overlap token."""


class StepFlagConflictError(RecipeContractError):
    """A step declares both ``required`` and ``diagnostic_only``."""


class UnknownRecipeTaskKindError(RecipeContractError):
    """A step names a task kind outside the fifteen-kind inventory."""

    code = "unknown_implementation"

    def __init__(self, task_kind: object, *, step_id: str | None = None) -> None:
        """Create the error for an unknown *task_kind*."""
        super().__init__(
            f"task kind {task_kind!r} is not one of the fifteen existing task kinds",
            detail={
                "capability_id": "<task_kind_reference>",
                "implementation_id": str(task_kind),
                "step_id": step_id,
            },
        )


# ---------------------------------------------------------------------------
# Standard-library shape helpers
# ---------------------------------------------------------------------------


def _schema_error(where: str, message: str) -> RecipeSchemaError:
    return RecipeSchemaError(f"{where}: {message}")


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _schema_error(where, f"expected a JSON object, got {type(value).__name__}")
    return {str(key): item for key, item in value.items()}


def _as_sequence(value: Any, where: str) -> list[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _schema_error(where, f"expected a JSON array, got {type(value).__name__}")
    return list(value)


def _as_string(value: Any, where: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _schema_error(where, f"expected a string, got {type(value).__name__}")
    if not allow_empty and not value:
        raise _schema_error(where, "string must not be empty")
    return value


def _as_optional_string(value: Any, where: str) -> str | None:
    if value is None:
        return None
    return _as_string(value, where)


def _as_bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise _schema_error(where, f"expected a boolean, got {type(value).__name__}")
    return value


def _as_int(value: Any, where: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _schema_error(where, f"expected an integer, got {type(value).__name__}")
    if minimum is not None and value < minimum:
        raise _schema_error(where, f"{value} is below the minimum {minimum}")
    return value


def _as_string_tuple(value: Any, where: str) -> tuple[str, ...]:
    return tuple(
        _as_string(item, f"{where}[{index}]")
        for index, item in enumerate(_as_sequence(value, where))
    )


def _check_safe_field_name(name: str, where: str) -> None:
    if not isinstance(name, str) or not _SAFE_FIELD_RE.fullmatch(name):
        raise _schema_error(where, f"field name {name!r} is not lowercase snake_case")
    if _PLATFORM_TOKEN_RE.search(name):
        raise RecipeForbiddenFieldError(
            f"{where}: field name {name!r} carries a job/queue/account/price/status token"
        )


def _check_object_keys(data: Mapping[str, Any], where: str) -> None:
    for name in data:
        _check_safe_field_name(str(name), where)


def _require_keys(data: Mapping[str, Any], required: Sequence[str], where: str) -> None:
    missing = sorted(set(required) - set(data))
    if missing:
        raise _schema_error(where, f"missing required field(s) {missing}")


def _reject_unknown_keys(data: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise _schema_error(where, f"unknown field(s) {unknown}; canonical v1 payloads are closed")


def _check_step_id(value: str, where: str) -> str:
    step_id = _as_string(value, where)
    if not _STEP_ID_RE.fullmatch(step_id):
        raise _schema_error(where, f"step id {step_id!r} does not match {STEP_ID_PATTERN}")
    return step_id


def _check_parameters(value: Any, where: str) -> MappingProxyType[str, Any] | None:
    if value is None:
        return None
    data = _as_mapping(value, where)
    _check_object_keys(data, where)
    normalized: dict[str, Any] = {}
    for key in sorted(data):
        normalized[key] = _check_parameter_value(data[key], f"{where}.{key}")
    return MappingProxyType(normalized)


def _check_parameter_value(value: Any, where: str) -> Any:
    if isinstance(value, (list, tuple)):
        return [
            _check_parameter_value(item, f"{where}[{index}]") for index, item in enumerate(value)
        ]
    if value is None or isinstance(value, _SCALAR_TYPES):
        return value
    raise _schema_error(where, f"unsupported parameter value type {type(value).__name__}")


def _serialize_parameters(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {key: value[key] for key in sorted(value)}


@lru_cache(maxsize=1)
def existing_task_kinds() -> tuple[str, ...]:
    """Return the fifteen-kind inventory owned by the capability binding.

    The list is imported lazily from
    :mod:`cccp.calculation.capabilities.task_binding`; this module never
    re-declares the inventory as a second authority.
    """
    from cccp.calculation.capabilities.task_binding import EXISTING_TASK_KINDS

    return tuple(EXISTING_TASK_KINDS)


# ---------------------------------------------------------------------------
# Contract types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Selector:
    """Typed selector over one upstream product."""

    selector_kind: str
    index: int | None = None
    range: tuple[int, int] | None = None
    field: str | None = None

    def __post_init__(self) -> None:
        if self.selector_kind not in SELECTOR_KINDS:
            raise _schema_error(
                "selector.selector_kind",
                f"must be one of {sorted(SELECTOR_KINDS)}, got {self.selector_kind!r}",
            )
        index = None if self.index is None else _as_int(self.index, "selector.index", minimum=0)
        field_name = None
        if self.field is not None:
            field_name = _as_string(self.field, "selector.field")
            _check_safe_field_name(field_name, "selector.field")
        bounds: tuple[int, int] | None = None
        if self.range is not None:
            raw = _as_sequence(self.range, "selector.range")
            if len(raw) != 2:
                raise _schema_error("selector.range", "must contain exactly two integers")
            bounds = (
                _as_int(raw[0], "selector.range[0]", minimum=0),
                _as_int(raw[1], "selector.range[1]", minimum=0),
            )
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "range", bounds)
        object.__setattr__(self, "field", field_name)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped selector mapping."""
        payload: dict[str, Any] = {"selector_kind": self.selector_kind}
        if self.index is not None:
            payload["index"] = self.index
        if self.range is not None:
            payload["range"] = list(self.range)
        if self.field is not None:
            payload["field"] = self.field
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "selector") -> Selector:
        """Build a selector from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _SELECTOR_KEYS, where)
        _require_keys(data, ("selector_kind",), where)
        return cls(
            selector_kind=_as_string(data["selector_kind"], f"{where}.selector_kind"),
            index=data.get("index"),
            range=tuple(data["range"]) if data.get("range") is not None else None,
            field=data.get("field"),
        )


_SELECTOR_KEYS: Final[tuple[str, ...]] = ("selector_kind", "index", "range", "field")


@dataclass(frozen=True, slots=True)
class OutputRef:
    """A typed reference to one product of an upstream step."""

    from_step: str
    product_kind: str
    selector: Selector
    required: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "from_step",
            _check_step_id(self.from_step, "output_ref.from_step"),
        )
        object.__setattr__(
            self,
            "product_kind",
            _as_string(self.product_kind, "output_ref.product_kind"),
        )
        selector = (
            self.selector
            if isinstance(self.selector, Selector)
            else Selector.from_dict(_as_mapping(self.selector, "output_ref.selector"))
        )
        object.__setattr__(self, "selector", selector)
        if self.required is not None:
            object.__setattr__(
                self,
                "required",
                _as_bool(self.required, "output_ref.required"),
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped output-reference mapping."""
        payload: dict[str, Any] = {
            "from_step": self.from_step,
            "product_kind": self.product_kind,
            "selector": self.selector.to_dict(),
        }
        if self.required is not None:
            payload["required"] = self.required
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "output_ref") -> OutputRef:
        """Build an output reference from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _OUTPUT_REF_KEYS, where)
        _require_keys(data, ("from_step", "product_kind", "selector"), where)
        return cls(
            from_step=data["from_step"],
            product_kind=data["product_kind"],
            selector=Selector.from_dict(
                _as_mapping(data["selector"], f"{where}.selector"),
                f"{where}.selector",
            ),
            required=data.get("required"),
        )


_OUTPUT_REF_KEYS: Final[tuple[str, ...]] = (
    "from_step",
    "product_kind",
    "selector",
    "required",
)


@dataclass(frozen=True, slots=True)
class Edge:
    """A dependency edge between two recipe steps."""

    edge_id: str
    from_step: str
    to_step: str
    output_ref: OutputRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "edge_id", _as_string(self.edge_id, "edge.edge_id"))
        from_step = _check_step_id(self.from_step, "edge.from_step")
        to_step = _check_step_id(self.to_step, "edge.to_step")
        object.__setattr__(self, "from_step", from_step)
        object.__setattr__(self, "to_step", to_step)
        if self.output_ref is not None:
            output_ref = (
                self.output_ref
                if isinstance(self.output_ref, OutputRef)
                else OutputRef.from_dict(_as_mapping(self.output_ref, "edge.output_ref"))
            )
            if output_ref.from_step != from_step:
                raise _schema_error(
                    "edge.output_ref.from_step",
                    f"must equal edge.from_step {from_step!r}, got {output_ref.from_step!r}",
                )
            object.__setattr__(self, "output_ref", output_ref)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped edge mapping."""
        payload: dict[str, Any] = {
            "edge_id": self.edge_id,
            "from_step": self.from_step,
            "to_step": self.to_step,
        }
        if self.output_ref is not None:
            payload["output_ref"] = self.output_ref.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "edge") -> Edge:
        """Build an edge from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _EDGE_KEYS, where)
        _require_keys(data, ("edge_id", "from_step", "to_step"), where)
        return cls(
            edge_id=data["edge_id"],
            from_step=data["from_step"],
            to_step=data["to_step"],
            output_ref=(
                OutputRef.from_dict(
                    _as_mapping(data["output_ref"], f"{where}.output_ref"),
                    f"{where}.output_ref",
                )
                if data.get("output_ref") is not None
                else None
            ),
        )


_EDGE_KEYS: Final[tuple[str, ...]] = ("edge_id", "from_step", "to_step", "output_ref")


@dataclass(frozen=True, slots=True)
class Applicability:
    """Declared scientific applicability of a recipe or step."""

    supported_reaction_classes: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    unsupported_when: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "supported_reaction_classes",
            _as_string_tuple(
                self.supported_reaction_classes,
                "applicability.supported_reaction_classes",
            ),
        )
        object.__setattr__(
            self,
            "preconditions",
            _as_string_tuple(self.preconditions, "applicability.preconditions"),
        )
        object.__setattr__(
            self,
            "unsupported_when",
            _as_string_tuple(self.unsupported_when, "applicability.unsupported_when"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped applicability mapping."""
        return {
            "supported_reaction_classes": list(self.supported_reaction_classes),
            "preconditions": list(self.preconditions),
            "unsupported_when": list(self.unsupported_when),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "applicability") -> Applicability:
        """Build applicability from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _APPLICABILITY_KEYS, where)
        _require_keys(data, ("supported_reaction_classes", "preconditions"), where)
        return cls(
            supported_reaction_classes=_as_string_tuple(
                data["supported_reaction_classes"],
                f"{where}.supported_reaction_classes",
            ),
            preconditions=_as_string_tuple(data["preconditions"], f"{where}.preconditions"),
            unsupported_when=_as_string_tuple(
                data.get("unsupported_when", ()),
                f"{where}.unsupported_when",
            ),
        )


_APPLICABILITY_KEYS: Final[tuple[str, ...]] = (
    "supported_reaction_classes",
    "preconditions",
    "unsupported_when",
)


@dataclass(frozen=True, slots=True)
class ValidatorRef:
    """A versioned validator reference declared by a recipe or step."""

    validator_id: str
    validator_version: str
    required: bool

    def __post_init__(self) -> None:
        validator_id = _as_string(self.validator_id, "validator.validator_id")
        if not _VALIDATOR_ID_RE.fullmatch(validator_id):
            raise _schema_error(
                "validator.validator_id",
                f"{validator_id!r} must start with check./software./human.",
            )
        version = _as_string(self.validator_version, "validator.validator_version")
        if not _VALIDATOR_VERSION_RE.fullmatch(version):
            raise _schema_error(
                "validator.validator_version",
                f"{version!r} must be a version string",
            )
        object.__setattr__(self, "validator_id", validator_id)
        object.__setattr__(self, "validator_version", version)
        object.__setattr__(self, "required", _as_bool(self.required, "validator.required"))

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped validator mapping."""
        return {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "required": self.required,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "validator") -> ValidatorRef:
        """Build a validator reference from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _VALIDATOR_KEYS, where)
        _require_keys(data, _VALIDATOR_KEYS, where)
        return cls(
            validator_id=data["validator_id"],
            validator_version=data["validator_version"],
            required=data["required"],
        )


_VALIDATOR_KEYS: Final[tuple[str, ...]] = (
    "validator_id",
    "validator_version",
    "required",
)


@dataclass(frozen=True, slots=True)
class Step:
    """One scientific step: a capability reference or an existing task kind."""

    step_id: str
    intent: str
    required: bool
    diagnostic_only: bool
    capability_ref: str | None = None
    task_kind: str | None = None
    parameters: Mapping[str, Any] | None = None
    consumes: tuple[OutputRef, ...] = ()
    produces: tuple[OutputRef, ...] = ()
    validators: tuple[ValidatorRef, ...] = ()
    applicability: Applicability | None = None
    alternatives: tuple[Alternative, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _check_step_id(self.step_id, "step.step_id"))
        object.__setattr__(self, "intent", _as_string(self.intent, "step.intent"))
        required = _as_bool(self.required, "step.required")
        diagnostic_only = _as_bool(self.diagnostic_only, "step.diagnostic_only")
        if required and diagnostic_only:
            raise StepFlagConflictError(
                f"step {self.step_id!r} declares both required and diagnostic_only",
                detail={"step_id": self.step_id, "required": required, "diagnostic_only": True},
            )
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "diagnostic_only", diagnostic_only)

        capability_ref = (
            None
            if self.capability_ref is None
            else _as_string(self.capability_ref, "step.capability_ref")
        )
        task_kind = None if self.task_kind is None else _as_string(self.task_kind, "step.task_kind")
        if (capability_ref is None) == (task_kind is None):
            raise _schema_error(
                "step",
                "exactly one of capability_ref and task_kind must be a non-null value",
            )
        if task_kind is not None and task_kind not in existing_task_kinds():
            raise UnknownRecipeTaskKindError(task_kind, step_id=self.step_id)
        object.__setattr__(self, "capability_ref", capability_ref)
        object.__setattr__(self, "task_kind", task_kind)
        object.__setattr__(
            self,
            "parameters",
            _check_parameters(self.parameters, "step.parameters"),
        )

        consumes = tuple(
            item
            if isinstance(item, OutputRef)
            else OutputRef.from_dict(_as_mapping(item, "step.consumes"), "step.consumes")
            for item in self.consumes
        )
        produces = tuple(
            item
            if isinstance(item, OutputRef)
            else OutputRef.from_dict(_as_mapping(item, "step.produces"), "step.produces")
            for item in self.produces
        )
        for output in produces:
            if output.from_step != self.step_id:
                raise _schema_error(
                    "step.produces.from_step",
                    f"must equal step_id {self.step_id!r}, got {output.from_step!r}",
                )
        validators = tuple(
            item
            if isinstance(item, ValidatorRef)
            else ValidatorRef.from_dict(_as_mapping(item, "step.validators"), "step.validators")
            for item in self.validators
        )
        applicability = self.applicability
        if applicability is not None and not isinstance(applicability, Applicability):
            applicability = Applicability.from_dict(
                _as_mapping(applicability, "step.applicability")
            )
        alternatives = tuple(
            item
            if isinstance(item, Alternative)
            else Alternative.from_dict(_as_mapping(item, "step.alternatives"), "step.alternatives")
            for item in self.alternatives
        )
        object.__setattr__(self, "consumes", consumes)
        object.__setattr__(self, "produces", produces)
        object.__setattr__(self, "validators", validators)
        object.__setattr__(self, "applicability", applicability)
        object.__setattr__(self, "alternatives", alternatives)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped step mapping."""
        payload: dict[str, Any] = {
            "step_id": self.step_id,
            "intent": self.intent,
            "required": self.required,
            "diagnostic_only": self.diagnostic_only,
            "capability_ref": self.capability_ref,
            "task_kind": self.task_kind,
            "parameters": _serialize_parameters(self.parameters),
            "consumes": [output.to_dict() for output in self.consumes],
            "produces": [output.to_dict() for output in self.produces],
            "validators": [validator.to_dict() for validator in self.validators],
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
        }
        if self.applicability is not None:
            payload["applicability"] = self.applicability.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "step") -> Step:
        """Build a step from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _STEP_KEYS, where)
        _require_keys(data, ("step_id", "intent", "required", "diagnostic_only"), where)
        return cls(
            step_id=data["step_id"],
            intent=data["intent"],
            required=data["required"],
            diagnostic_only=data["diagnostic_only"],
            capability_ref=data.get("capability_ref"),
            task_kind=data.get("task_kind"),
            parameters=data.get("parameters"),
            consumes=tuple(
                OutputRef.from_dict(
                    _as_mapping(item, f"{where}.consumes[{index}]"),
                    f"{where}.consumes[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data.get("consumes", ()), f"{where}.consumes")
                )
            ),
            produces=tuple(
                OutputRef.from_dict(
                    _as_mapping(item, f"{where}.produces[{index}]"),
                    f"{where}.produces[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data.get("produces", ()), f"{where}.produces")
                )
            ),
            validators=tuple(
                ValidatorRef.from_dict(
                    _as_mapping(item, f"{where}.validators[{index}]"),
                    f"{where}.validators[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data.get("validators", ()), f"{where}.validators")
                )
            ),
            applicability=(
                Applicability.from_dict(
                    _as_mapping(data["applicability"], f"{where}.applicability"),
                    f"{where}.applicability",
                )
                if data.get("applicability") is not None
                else None
            ),
            alternatives=tuple(
                Alternative.from_dict(
                    _as_mapping(item, f"{where}.alternatives[{index}]"),
                    f"{where}.alternatives[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data.get("alternatives", ()), f"{where}.alternatives")
                )
            ),
        )


_STEP_KEYS: Final[tuple[str, ...]] = (
    "step_id",
    "intent",
    "required",
    "diagnostic_only",
    "capability_ref",
    "task_kind",
    "parameters",
    "consumes",
    "produces",
    "validators",
    "applicability",
    "alternatives",
)


@dataclass(frozen=True, slots=True)
class Alternative:
    """A bounded alternative block that may replace a main step."""

    alternative_id: str
    when: str
    max_uses: int
    steps: tuple[Step, ...]
    replaces_step_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "alternative_id",
            _as_string(self.alternative_id, "alternative.alternative_id"),
        )
        object.__setattr__(self, "when", _as_string(self.when, "alternative.when"))
        object.__setattr__(
            self,
            "max_uses",
            _as_int(self.max_uses, "alternative.max_uses", minimum=1),
        )
        steps = tuple(
            item
            if isinstance(item, Step)
            else Step.from_dict(_as_mapping(item, "alternative.steps"), "alternative.steps")
            for item in self.steps
        )
        if not steps:
            raise _schema_error("alternative.steps", "must contain at least one step")
        replaces = self.replaces_step_id
        if replaces is not None:
            replaces = _check_step_id(replaces, "alternative.replaces_step_id")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "replaces_step_id", replaces)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped alternative mapping."""
        payload: dict[str, Any] = {
            "alternative_id": self.alternative_id,
            "when": self.when,
            "max_uses": self.max_uses,
            "steps": [step.to_dict() for step in self.steps],
        }
        if self.replaces_step_id is not None:
            payload["replaces_step_id"] = self.replaces_step_id
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "alternative") -> Alternative:
        """Build an alternative from its canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _ALTERNATIVE_KEYS, where)
        _require_keys(data, ("alternative_id", "when", "max_uses", "steps"), where)
        return cls(
            alternative_id=data["alternative_id"],
            when=data["when"],
            max_uses=data["max_uses"],
            steps=tuple(
                Step.from_dict(
                    _as_mapping(item, f"{where}.steps[{index}]"),
                    f"{where}.steps[{index}]",
                )
                for index, item in enumerate(_as_sequence(data["steps"], f"{where}.steps"))
            ),
            replaces_step_id=data.get("replaces_step_id"),
        )


_ALTERNATIVE_KEYS: Final[tuple[str, ...]] = (
    "alternative_id",
    "when",
    "max_uses",
    "steps",
    "replaces_step_id",
)


@dataclass(frozen=True, slots=True)
class Limits:
    """Declared size bounds for a recipe."""

    max_steps: int
    max_edges: int | None = None
    max_alternatives: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_steps",
            _as_int(self.max_steps, "limits.max_steps", minimum=1),
        )
        if self.max_edges is not None:
            object.__setattr__(
                self,
                "max_edges",
                _as_int(self.max_edges, "limits.max_edges", minimum=0),
            )
        if self.max_alternatives is not None:
            object.__setattr__(
                self,
                "max_alternatives",
                _as_int(self.max_alternatives, "limits.max_alternatives", minimum=0),
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped limits mapping."""
        payload: dict[str, Any] = {"max_steps": self.max_steps}
        if self.max_edges is not None:
            payload["max_edges"] = self.max_edges
        if self.max_alternatives is not None:
            payload["max_alternatives"] = self.max_alternatives
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "limits") -> Limits:
        """Build limits from their canonical mapping."""
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        _reject_unknown_keys(data, _LIMITS_KEYS, where)
        _require_keys(data, ("max_steps",), where)
        return cls(
            max_steps=data["max_steps"],
            max_edges=data.get("max_edges"),
            max_alternatives=data.get("max_alternatives"),
        )


_LIMITS_KEYS: Final[tuple[str, ...]] = ("max_steps", "max_edges", "max_alternatives")


@dataclass(frozen=True, slots=True)
class Recipe:
    """Frozen ACP2 scientific recipe (``recipe.schema.json`` version 1)."""

    recipe_id: str
    applicability: Applicability
    steps: tuple[Step, ...]
    edges: tuple[Edge, ...]
    limits: Limits
    validators: tuple[ValidatorRef, ...] = ()
    alternatives: tuple[Alternative, ...] = ()
    title: str | None = None
    science_identity: str | None = None
    schema_version: int = RECIPE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if (
            isinstance(self.schema_version, bool)
            or not isinstance(self.schema_version, int)
            or self.schema_version != RECIPE_SCHEMA_VERSION
        ):
            raise RecipeSchemaVersionError(
                f"unsupported recipe schema_version {self.schema_version!r}; "
                f"only {RECIPE_SCHEMA_VERSION} is readable as a canonical recipe",
                detail={
                    "expected": RECIPE_SCHEMA_VERSION,
                    "actual": self.schema_version,
                },
            )
        object.__setattr__(
            self,
            "recipe_id",
            _as_string(self.recipe_id, "recipe.recipe_id"),
        )
        applicability = self.applicability
        if not isinstance(applicability, Applicability):
            applicability = Applicability.from_dict(
                _as_mapping(applicability, "recipe.applicability")
            )
        object.__setattr__(self, "applicability", applicability)

        steps = tuple(
            item
            if isinstance(item, Step)
            else Step.from_dict(_as_mapping(item, "recipe.steps"), "recipe.steps")
            for item in self.steps
        )
        if not steps:
            raise _schema_error("recipe.steps", "must contain at least one step")
        object.__setattr__(self, "steps", steps)

        edges = tuple(
            item
            if isinstance(item, Edge)
            else Edge.from_dict(_as_mapping(item, "recipe.edges"), "recipe.edges")
            for item in self.edges
        )
        object.__setattr__(self, "edges", edges)

        limits = self.limits
        if not isinstance(limits, Limits):
            limits = Limits.from_dict(_as_mapping(limits, "recipe.limits"), "recipe.limits")
        object.__setattr__(self, "limits", limits)

        validators = tuple(
            item
            if isinstance(item, ValidatorRef)
            else ValidatorRef.from_dict(_as_mapping(item, "recipe.validators"), "recipe.validators")
            for item in self.validators
        )
        alternatives = tuple(
            item
            if isinstance(item, Alternative)
            else Alternative.from_dict(
                _as_mapping(item, "recipe.alternatives"), "recipe.alternatives"
            )
            for item in self.alternatives
        )
        object.__setattr__(self, "validators", validators)
        object.__setattr__(self, "alternatives", alternatives)

        if self.title is not None:
            object.__setattr__(self, "title", _as_string(self.title, "recipe.title"))
        if self.science_identity is not None:
            identity = _as_string(self.science_identity, "recipe.science_identity")
            if not _SCIENCE_IDENTITY_RE.fullmatch(identity):
                raise _schema_error(
                    "recipe.science_identity",
                    f"{identity!r} must match v2:<32 lowercase hex characters>",
                )
            object.__setattr__(self, "science_identity", identity)

    def to_dict(self) -> dict[str, Any]:
        """Return the schema-shaped recipe mapping."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "recipe_id": self.recipe_id,
            "applicability": self.applicability.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "edges": [edge.to_dict() for edge in self.edges],
            "validators": [validator.to_dict() for validator in self.validators],
            "alternatives": [alternative.to_dict() for alternative in self.alternatives],
            "limits": self.limits.to_dict(),
        }
        if self.title is not None:
            payload["title"] = self.title
        if self.science_identity is not None:
            payload["science_identity"] = self.science_identity
        return payload

    def to_json(self) -> str:
        """Return the frozen canonical JSON encoding of this recipe."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], where: str = "recipe") -> Recipe:
        """Build a recipe from its canonical mapping.

        Raises:
            RecipeSchemaVersionError: ``schema_version`` is not 1.
            RecipeSchemaError: The object shape, regex or bound is invalid.
            RecipeForbiddenFieldError: A field name carries a forbidden token.
        """
        data = _as_mapping(payload, where)
        _check_object_keys(data, where)
        version = data.get("schema_version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != RECIPE_SCHEMA_VERSION
        ):
            raise RecipeSchemaVersionError(
                f"{where}: unsupported schema_version {version!r}; "
                f"only {RECIPE_SCHEMA_VERSION} is readable as a canonical recipe",
                detail={"expected": RECIPE_SCHEMA_VERSION, "actual": version},
            )
        _reject_unknown_keys(data, _RECIPE_KEYS, where)
        _require_keys(data, _RECIPE_REQUIRED_KEYS, where)
        return cls(
            recipe_id=data["recipe_id"],
            applicability=Applicability.from_dict(
                _as_mapping(data["applicability"], f"{where}.applicability"),
                f"{where}.applicability",
            ),
            steps=tuple(
                Step.from_dict(
                    _as_mapping(item, f"{where}.steps[{index}]"),
                    f"{where}.steps[{index}]",
                )
                for index, item in enumerate(_as_sequence(data["steps"], f"{where}.steps"))
            ),
            edges=tuple(
                Edge.from_dict(
                    _as_mapping(item, f"{where}.edges[{index}]"),
                    f"{where}.edges[{index}]",
                )
                for index, item in enumerate(_as_sequence(data["edges"], f"{where}.edges"))
            ),
            limits=Limits.from_dict(
                _as_mapping(data["limits"], f"{where}.limits"),
                f"{where}.limits",
            ),
            validators=tuple(
                ValidatorRef.from_dict(
                    _as_mapping(item, f"{where}.validators[{index}]"),
                    f"{where}.validators[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data["validators"], f"{where}.validators")
                )
            ),
            alternatives=tuple(
                Alternative.from_dict(
                    _as_mapping(item, f"{where}.alternatives[{index}]"),
                    f"{where}.alternatives[{index}]",
                )
                for index, item in enumerate(
                    _as_sequence(data["alternatives"], f"{where}.alternatives")
                )
            ),
            title=data.get("title"),
            science_identity=data.get("science_identity"),
            schema_version=RECIPE_SCHEMA_VERSION,
        )

    @classmethod
    def from_json(cls, text: str, where: str = "recipe") -> Recipe:
        """Parse canonical JSON text into a recipe."""
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise _schema_error(where, f"invalid JSON: {exc}") from exc
        return cls.from_dict(_as_mapping(payload, where), where)


_RECIPE_REQUIRED_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "recipe_id",
    "applicability",
    "steps",
    "edges",
    "validators",
    "alternatives",
    "limits",
)
_RECIPE_KEYS: Final[tuple[str, ...]] = (*_RECIPE_REQUIRED_KEYS, "title", "science_identity")
