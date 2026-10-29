"""Deterministic recipe compiler and science-identity boundary (A-07b).

The compiler turns a validated :class:`~cccp.calculation.recipes.contracts.Recipe`
plus its frozen initial inputs into a *definition*:

* one :class:`~cccp.calculation.requests.TaskRequest` (or the existing
  capability/task binding output for a pure-analysis step) per recipe step;
* canonical JSON for the whole definition;
* a plan-definition digest frozen **before approval**;
* a step-execution identity materialized **only at dispatch time**.

It deliberately stops there.  It never executes a step, never starts a
subprocess, never writes an artifact, never persists a checkpoint and never
retries.  The execution loop, job persistence, cancellation and platform
retry policy belong to Runtime (B18); this module is not a second dispatcher.

Two identities, one science scheme
----------------------------------

Both identities reuse the frozen primitives in
:mod:`cccp.analysis.identity` (``identity_fingerprint`` / ``content_sha256``)
and the shared canonical JSON encoding.  No second hashing scheme is added.

*Plan-definition digest* (``compile_recipe``): a ``v2:`` fingerprint over the
recipe, initial inputs, method, provider/plugin versions, step parameters,
``OutputRef`` selectors and declared bounded alternatives.  It excludes
``title``/display name, cached ``science_identity``, approval fields, evidence
references, timestamps, job ids and execution resources.

*Step-execution identity* (:func:`materialize_step_identity`): a ``v2:``
fingerprint over the plan-definition digest, the materialized science inputs,
the actual upstream content digests, the materialized parameters and the
provider/implementation versions present at dispatch.  An unresolved
``OutputRef`` selector or a missing upstream digest is a typed error, never a
silent skip.

Unknown scientific fields are rejected for new execution.  Historical payloads
are read by :func:`read_historical_recipe`, which preserves every unknown field
and marks the result non-executable and non-recoverable.

Author: QCcalc Team
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Final

from cccp.analysis.identity import (
    IDENTITY_SCHEMA,
    IdentityInputMissing,
    content_sha256,
    identity_fingerprint,
)
from cccp.analysis.research._common import canonical_json
from cccp.calculation.capabilities.builtin import builtin_registry
from cccp.calculation.capabilities.contracts import CapabilityKind
from cccp.calculation.capabilities.registry import CapabilityRegistry
from cccp.calculation.capabilities.task_binding import (
    BindingSelection,
    TaskBinding,
    bind_capability,
    bind_task_kind,
)
from cccp.calculation.contracts import (
    ElectronicStateSpec,
    electronic_state_spec_from_dict,
    electronic_state_spec_to_dict,
)
from cccp.calculation.errors import TaskInputError
from cccp.calculation.recipes.contracts import (
    Alternative,
    Edge,
    OutputRef,
    Recipe,
    RecipeContractError,
    Selector,
    Step,
    ValidatorRef,
)
from cccp.calculation.recipes.validation import (
    RecipeValidationReport,
    validate_recipe,
)
from cccp.calculation.requests import (
    TASK_OPTIONS_TYPES,
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskOptions,
    TaskRequest,
    options_to_dict,
)

logger = logging.getLogger(__name__)

__all__ = [
    "COMPILED_RECIPE_SCHEMA",
    "COMPILED_RECIPE_SCHEMA_VERSION",
    "WHOLE_STEP_PRODUCT_KIND",
    "ApprovalMetadata",
    "CompileInputError",
    "CompiledRecipe",
    "CompiledStep",
    "HistoricalRecipe",
    "InvalidProviderVersionError",
    "InvalidUpstreamDigestError",
    "RecipeCompileError",
    "RecipeInputs",
    "StepExecutionIdentity",
    "StepIdentityError",
    "UnknownCompiledStepError",
    "UnknownScientificFieldError",
    "UnresolvedOutputRefError",
    "compile_recipe",
    "materialize_step_identity",
    "output_ref_address",
    "read_historical_recipe",
]

#: Identity of the compiled definition document (not a recovery key).
COMPILED_RECIPE_SCHEMA = "acp2.science.compiled_recipe"
COMPILED_RECIPE_SCHEMA_VERSION = 1

#: Product kind used when an edge declares a dependency without an
#: ``OutputRef``: the upstream step's whole scientific result.
WHOLE_STEP_PRODUCT_KIND = "step_result"

_INPUT_SCHEMA_VERSION: Final = 1
_PLATFORM_TOKEN_RE: Final = re.compile(r"(job|queue|account|price|status)")
_SAFE_FIELD_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

_STRUCTURE_INPUT_KEYS: Final[tuple[str, ...]] = (
    "path",
    "coordinates",
    "symbols",
    "elements",
    "role",
    "source",
)
_INPUT_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "structure",
    "charge",
    "multiplicity",
    "method",
    "backend",
    "electronic_state",
    "standard_state",
    "atom_mapping",
    "comparison_conditions",
    "provider_versions",
)
_APPROVAL_KEYS: Final[tuple[str, ...]] = (
    "approval_ref",
    "approval_status",
    "evidence_refs",
    "display_name",
    "timestamp",
)


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class RecipeCompileError(RecipeContractError):
    """Base class for a recipe that cannot be compiled for execution."""

    code = "invalid_scientific_input"


class UnknownScientificFieldError(RecipeCompileError):
    """A payload carries a field the execution contract does not understand."""

    def __init__(self, field_name: str, *, where: str, expected: Sequence[str]) -> None:
        """Create the error naming the unknown field and its closed vocabulary."""
        super().__init__(
            f"{where}: unknown scientific field {field_name!r}; executable "
            "payloads are closed and unknown fields are never ignored",
            detail={
                "field": f"{where}.{field_name}",
                "reason": "unknown_scientific_field",
                "expected": sorted(expected),
                "actual": field_name,
            },
        )


class CompileInputError(RecipeCompileError):
    """Initial inputs or a step parameter violate the request contract."""

    def __init__(self, message: str, *, detail: Mapping[str, object] | None = None) -> None:
        """Create the error with structured detail."""
        super().__init__(
            message,
            detail={
                "field": "recipe.inputs",
                "reason": "invalid_compile_input",
                **(dict(detail or {})),
            },
        )


class StepIdentityError(RecipeCompileError):
    """Base class for a step identity that cannot be materialized."""


class UnknownCompiledStepError(StepIdentityError):
    """The requested step id is not part of the compiled definition."""

    def __init__(self, step_id: str) -> None:
        """Create the error for *step_id*."""
        super().__init__(
            f"compiled recipe has no step {step_id!r}",
            detail={
                "field": "step_id",
                "reason": "unknown_compiled_step",
                "expected": "a declared compiled step id",
                "actual": step_id,
            },
        )


class UnresolvedOutputRefError(StepIdentityError):
    """An ``OutputRef`` selector or its upstream digest is not resolvable."""

    def __init__(
        self,
        *,
        step_id: str,
        reference: OutputRef,
        reason: str,
        expected: object,
        actual: object,
    ) -> None:
        """Create the dispatch-time error for one unresolved reference."""
        address = output_ref_address(reference)
        super().__init__(
            f"step {step_id!r} cannot materialize dependency {address!r}: {reason}",
            detail={
                "field": f"steps[{step_id}].consumes",
                "reason": reason,
                "expected": expected,
                "actual": actual,
                "step_id": step_id,
                "from_step": reference.from_step,
                "product_kind": reference.product_kind,
                "selector_address": address,
            },
        )


class InvalidUpstreamDigestError(StepIdentityError):
    """A materialized upstream digest is not a SHA-256 content digest."""

    def __init__(self, *, address: str, actual: object) -> None:
        """Create the error for one malformed digest."""
        super().__init__(
            f"dependency {address!r} carries an invalid upstream digest",
            detail={
                "field": "upstream_digests",
                "reason": "invalid_upstream_digest",
                "expected": "sha256:<64 lowercase hex> or <64 lowercase hex>",
                "actual": actual,
                "selector_address": address,
            },
        )


class InvalidProviderVersionError(StepIdentityError):
    """Provider/plugin versions are not a mapping of non-empty strings."""

    def __init__(self, *, actual: object) -> None:
        """Create the error for a malformed provider-version mapping."""
        super().__init__(
            "provider/plugin versions must be a mapping of non-empty strings",
            detail={
                "field": "provider_versions",
                "reason": "invalid_provider_versions",
                "expected": "mapping[str, str]",
                "actual": actual,
            },
        )


# ---------------------------------------------------------------------------
# Small closed-shape validators
# ---------------------------------------------------------------------------


def _as_string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise CompileInputError(
            f"{where} must be a non-empty string",
            detail={"field": where, "actual": value},
        )
    return value


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CompileInputError(
            f"{where} must be an integer",
            detail={"field": where, "actual": value},
        )
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: Sequence[str], where: str) -> None:
    for key in data:
        if str(key) not in allowed:
            raise UnknownScientificFieldError(str(key), where=where, expected=allowed)


def _check_field_name(name: str, where: str) -> str:
    if not _SAFE_FIELD_RE.fullmatch(name) or _PLATFORM_TOKEN_RE.search(name):
        raise CompileInputError(
            f"{where} name {name!r} is not a science-only lowercase snake_case name",
            detail={"field": where, "actual": name},
        )
    return name


# ---------------------------------------------------------------------------
# Initial inputs
# ---------------------------------------------------------------------------


def _freeze_sorted_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a sorted, read-only copy of *value*."""
    return MappingProxyType({key: value[key] for key in sorted(value)})


@dataclass(frozen=True, slots=True)
class RecipeInputs:
    """Frozen initial scientific inputs for one recipe definition.

    The fields are exactly the science-bearing inputs that must exist before
    approval.  Execution resources, work directories, job ids and display
    names are deliberately absent: they are platform/runtime state and never
    enter either identity.
    """

    structure: StructureInput | None = None
    charge: int = 0
    multiplicity: int = 1
    method: MethodSpec = MethodSpec()
    backend: str | None = None
    electronic_state: ElectronicStateSpec | None = None
    standard_state: str | None = None
    atom_mapping: tuple[int, ...] | None = None
    comparison_conditions: Mapping[str, Any] | None = None
    provider_versions: Mapping[str, str] | None = None
    schema_version: int = _INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != _INPUT_SCHEMA_VERSION:
            raise CompileInputError(
                f"unsupported inputs schema_version {self.schema_version!r}",
                detail={
                    "field": "inputs.schema_version",
                    "expected": _INPUT_SCHEMA_VERSION,
                    "actual": self.schema_version,
                },
            )
        object.__setattr__(self, "charge", _as_int(self.charge, "inputs.charge"))
        object.__setattr__(
            self,
            "multiplicity",
            _as_int(self.multiplicity, "inputs.multiplicity"),
        )

        structure = self.structure
        if structure is not None and not isinstance(structure, StructureInput):
            data = dict(structure) if isinstance(structure, Mapping) else None
            if data is None:
                raise CompileInputError(
                    "inputs.structure must be a StructureInput or a mapping",
                    detail={"field": "inputs.structure", "actual": type(structure).__name__},
                )
            _reject_unknown(data, _STRUCTURE_INPUT_KEYS, "inputs.structure")
            try:
                structure = StructureInput.from_dict(data)
            except (TaskInputError, ValueError) as exc:
                raise CompileInputError(
                    f"inputs.structure is invalid: {exc}",
                    detail={"field": "inputs.structure"},
                ) from exc
        object.__setattr__(self, "structure", structure)

        method = self.method
        if method is None:
            method = MethodSpec()
        elif not isinstance(method, MethodSpec):
            data = dict(method) if isinstance(method, Mapping) else None
            if data is None:
                raise CompileInputError(
                    "inputs.method must be a MethodSpec or a mapping",
                    detail={"field": "inputs.method", "actual": type(method).__name__},
                )
            _reject_unknown(data, _method_field_names(), "inputs.method")
            try:
                method = MethodSpec.from_dict(data)
            except (TaskInputError, ValueError) as exc:
                raise CompileInputError(
                    f"inputs.method is invalid: {exc}",
                    detail={"field": "inputs.method"},
                ) from exc
        object.__setattr__(self, "method", method)

        if self.backend is not None:
            object.__setattr__(
                self,
                "backend",
                _as_string(self.backend, "inputs.backend"),
            )
        if self.standard_state is not None:
            object.__setattr__(
                self,
                "standard_state",
                _as_string(self.standard_state, "inputs.standard_state"),
            )

        state = self.electronic_state
        if state is not None and not isinstance(state, ElectronicStateSpec):
            data = dict(state) if isinstance(state, Mapping) else None
            if data is None:
                raise CompileInputError(
                    "inputs.electronic_state must be an ElectronicStateSpec or a mapping",
                    detail={
                        "field": "inputs.electronic_state",
                        "actual": type(state).__name__,
                    },
                )
            _reject_unknown(data, _electronic_state_field_names(), "inputs.electronic_state")
            try:
                state = electronic_state_spec_from_dict(data)
            except (TaskInputError, ValueError) as exc:
                raise CompileInputError(
                    f"inputs.electronic_state is invalid: {exc}",
                    detail={"field": "inputs.electronic_state"},
                ) from exc
        object.__setattr__(self, "electronic_state", state)

        mapping = self.atom_mapping
        if mapping is not None:
            normalized = tuple(_as_int(item, "inputs.atom_mapping[]") for item in mapping)
            if any(item < 0 for item in normalized):
                raise CompileInputError(
                    "inputs.atom_mapping indices must be non-negative",
                    detail={"field": "inputs.atom_mapping", "actual": list(normalized)},
                )
            if len(set(normalized)) != len(normalized):
                raise CompileInputError(
                    "inputs.atom_mapping indices must be unique",
                    detail={"field": "inputs.atom_mapping", "actual": list(normalized)},
                )
            object.__setattr__(self, "atom_mapping", normalized)

        conditions = self.comparison_conditions
        if conditions is not None:
            normalized_conditions: dict[str, Any] = {}
            for raw_key, raw_value in conditions.items():
                key = _check_field_name(str(raw_key), "inputs.comparison_conditions")
                if not (raw_value is None or isinstance(raw_value, (str, int, float, bool))):
                    raise CompileInputError(
                        "comparison condition values must be JSON scalars",
                        detail={
                            "field": f"inputs.comparison_conditions.{key}",
                            "actual": type(raw_value).__name__,
                        },
                    )
                normalized_conditions[key] = raw_value
            object.__setattr__(
                self,
                "comparison_conditions",
                _freeze_sorted_mapping(normalized_conditions),
            )

        versions = self.provider_versions
        if versions is not None:
            normalized_versions: dict[str, str] = {}
            for raw_key, raw_value in versions.items():
                key = _check_field_name(str(raw_key), "inputs.provider_versions")
                normalized_versions[key] = _as_string(
                    raw_value,
                    f"inputs.provider_versions.{key}",
                )
            object.__setattr__(
                self,
                "provider_versions",
                _freeze_sorted_mapping(normalized_versions),
            )

    @classmethod
    def from_value(cls, value: RecipeInputs | Mapping[str, Any] | None) -> RecipeInputs:
        """Build inputs from ``None``, a typed instance or a closed mapping."""
        if value is None:
            return cls()
        if isinstance(value, RecipeInputs):
            return value
        if not isinstance(value, Mapping):
            raise CompileInputError(
                "recipe inputs must be a RecipeInputs or a mapping",
                detail={"field": "inputs", "actual": type(value).__name__},
            )
        data = {str(key): item for key, item in value.items()}
        _reject_unknown(data, _INPUT_KEYS, "inputs")
        version = data.get("schema_version", _INPUT_SCHEMA_VERSION)
        return cls(
            structure=data.get("structure"),
            charge=data.get("charge", 0),
            multiplicity=data.get("multiplicity", 1),
            method=data.get("method"),
            backend=data.get("backend"),
            electronic_state=data.get("electronic_state"),
            standard_state=data.get("standard_state"),
            atom_mapping=data.get("atom_mapping"),
            comparison_conditions=data.get("comparison_conditions"),
            provider_versions=data.get("provider_versions"),
            schema_version=version,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe input document (may contain a source path)."""
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "method": self.method.to_dict(),
        }
        if self.structure is not None:
            payload["structure"] = self.structure.to_dict()
        if self.backend is not None:
            payload["backend"] = self.backend
        if self.electronic_state is not None:
            payload["electronic_state"] = electronic_state_spec_to_dict(self.electronic_state)
        if self.standard_state is not None:
            payload["standard_state"] = self.standard_state
        if self.atom_mapping is not None:
            payload["atom_mapping"] = list(self.atom_mapping)
        if self.comparison_conditions:
            payload["comparison_conditions"] = dict(self.comparison_conditions)
        if self.provider_versions:
            payload["provider_versions"] = dict(self.provider_versions)
        return payload

    def identity_view(
        self,
        *,
        provider_versions: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Return the path-free, content-bound science view used by identities.

        A structure file is replaced by its content SHA-256; a machine path is
        never part of either identity.  ``provider_versions`` may be replaced
        at dispatch time so the step identity uses the versions actually
        present then.
        """
        payload: dict[str, Any] = {
            "charge": self.charge,
            "multiplicity": self.multiplicity,
            "method": self.method.to_dict(),
            "structure": _structure_identity_view(self.structure),
            "backend": self.backend,
            "electronic_state": (
                None
                if self.electronic_state is None
                else electronic_state_spec_to_dict(self.electronic_state)
            ),
            "standard_state": self.standard_state,
            "atom_mapping": None if self.atom_mapping is None else list(self.atom_mapping),
            "comparison_conditions": (
                None if not self.comparison_conditions else dict(self.comparison_conditions)
            ),
            "provider_versions": dict(
                provider_versions if provider_versions is not None else self.provider_versions or {}
            ),
        }
        return {key: value for key, value in payload.items() if value is not None}


def _method_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(MethodSpec))


def _electronic_state_field_names() -> tuple[str, ...]:
    return tuple(field.name for field in fields(ElectronicStateSpec))


def _option_field_names(task_kind: str) -> tuple[str, ...]:
    options_cls = TASK_OPTIONS_TYPES[TaskKind(task_kind)]
    return tuple(field.name for field in fields(options_cls))


def _structure_identity_view(structure: StructureInput | None) -> dict[str, Any] | None:
    """Return the content-bound structure view (never the file location)."""
    if structure is None:
        return None
    if structure.coordinates is not None and structure.symbols is not None:
        return {
            "kind": "inline",
            "role": structure.role.value,
            "symbols": list(structure.symbols),
            "coordinates": [
                [float(component) for component in row] for row in structure.coordinates
            ],
            "elements": list(structure.elements),
        }
    if structure.path is not None:
        try:
            digest = content_sha256(structure.path)
        except IdentityInputMissing as exc:
            raise CompileInputError(
                f"structure content is not readable for identity: {exc}",
                detail={"field": "inputs.structure.path"},
            ) from exc
        return {
            "kind": "file",
            "role": structure.role.value,
            "content_sha256": digest,
            "elements": list(structure.elements),
        }
    return {
        "kind": "unresolved",
        "role": structure.role.value,
        "elements": list(structure.elements),
    }


# ---------------------------------------------------------------------------
# Approval metadata (never part of either identity)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ApprovalMetadata:
    """Management metadata that must not invalidate approved science.

    Every field here is deliberately excluded from the plan-definition digest
    and from the step-execution identity.
    """

    approval_ref: str | None = None
    approval_status: str | None = None
    evidence_refs: tuple[str, ...] = ()
    display_name: str | None = None
    timestamp: str | None = None

    def __post_init__(self) -> None:
        for name in ("approval_ref", "approval_status", "display_name", "timestamp"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _as_string(value, f"approval.{name}"))
        if isinstance(self.evidence_refs, str) or not isinstance(self.evidence_refs, Sequence):
            raise CompileInputError(
                "approval.evidence_refs must be a sequence of strings",
                detail={"field": "approval.evidence_refs", "actual": self.evidence_refs},
            )
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(_as_string(item, "approval.evidence_refs[]") for item in self.evidence_refs),
        )

    @classmethod
    def from_value(
        cls,
        value: ApprovalMetadata | Mapping[str, Any] | None,
    ) -> ApprovalMetadata:
        """Build approval metadata from ``None``, an instance or a mapping."""
        if value is None:
            return cls()
        if isinstance(value, ApprovalMetadata):
            return value
        if not isinstance(value, Mapping):
            raise CompileInputError(
                "approval metadata must be an ApprovalMetadata or a mapping",
                detail={"field": "approval", "actual": type(value).__name__},
            )
        data = {str(key): item for key, item in value.items()}
        _reject_unknown(data, _APPROVAL_KEYS, "approval")
        return cls(
            approval_ref=data.get("approval_ref"),
            approval_status=data.get("approval_status"),
            evidence_refs=tuple(data.get("evidence_refs", ())),
            display_name=data.get("display_name"),
            timestamp=data.get("timestamp"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe management view."""
        payload: dict[str, Any] = {}
        for name in ("approval_ref", "approval_status", "display_name", "timestamp"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.evidence_refs:
            payload["evidence_refs"] = list(self.evidence_refs)
        return payload


# ---------------------------------------------------------------------------
# Compiled definition types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompiledStep:
    """One recipe step bound to exactly one existing task/capability output."""

    recipe_step: Step
    selection: BindingSelection
    task_kind: str | None
    binding_id: str
    delegation_target: str | None
    dispatchable: bool
    implementation_id: str
    request: TaskRequest | None
    request_view: Mapping[str, Any] | None
    parameters: Mapping[str, Any]
    #: Declared worst-case subprocess invocations for ONE dispatch of this step
    #: (A lane, M2-04c).  Reserved before the dispatch and drawn one unit at a
    #: time before every backend call.  Execution-resource data, therefore
    #: deliberately **not** part of either science identity.
    worst_case_executions: int = 1

    @property
    def step_id(self) -> str:
        """Return the stable recipe step id."""
        return self.recipe_step.step_id

    def to_dict(self) -> dict[str, Any]:
        """Return the definition view of this step."""
        payload: dict[str, Any] = {
            "step_id": self.step_id,
            "intent": self.recipe_step.intent,
            "required": self.recipe_step.required,
            "diagnostic_only": self.recipe_step.diagnostic_only,
            "selection": self.selection.to_dict(),
            "task_kind": self.task_kind,
            "binding_id": self.binding_id,
            "delegation_target": self.delegation_target,
            "dispatchable": self.dispatchable,
            "implementation_id": self.implementation_id,
            "parameters": dict(self.parameters),
            "consumes": [reference.to_dict() for reference in self.recipe_step.consumes],
            "produces": [reference.to_dict() for reference in self.recipe_step.produces],
            "validators": [validator.to_dict() for validator in self.recipe_step.validators],
            "alternatives": [
                alternative.to_dict() for alternative in self.recipe_step.alternatives
            ],
        }
        if self.recipe_step.applicability is not None:
            payload["applicability"] = self.recipe_step.applicability.to_dict()
        if self.request_view is not None:
            payload["request"] = copy.deepcopy(dict(self.request_view))
        return payload


@dataclass(frozen=True, slots=True)
class CompiledRecipe:
    """Canonical definition of one scientific recipe (never executable here)."""

    recipe_id: str
    schema_version: int
    recipe: Recipe
    inputs: RecipeInputs
    steps: tuple[CompiledStep, ...]
    edges: tuple[Edge, ...]
    alternatives: tuple[Alternative, ...]
    topological_order: tuple[str, ...]
    validation: RecipeValidationReport
    definition_json: str
    plan_definition_digest: str
    schema_digest: str
    approval: ApprovalMetadata = ApprovalMetadata()

    def step(self, step_id: str) -> CompiledStep:
        """Return the compiled step with *step_id*.

        Raises:
            UnknownCompiledStepError: No compiled step declares *step_id*.
        """
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise UnknownCompiledStepError(step_id)

    @property
    def worst_case_executions(self) -> int:
        """Sum of the declared per-step worst-case subprocess invocations.

        This is the number the runtime reserves **once** for the whole recipe
        dispatch (one reservation, drawn per backend call) instead of adding an
        implicit allowance per step.  It is execution-resource data and never
        enters a science identity.
        """
        return sum(int(step.worst_case_executions) for step in self.steps)

    def worst_case_by_step(self) -> dict[str, int]:
        """Return ``{step_id: worst_case_executions}`` (reservation view)."""
        return {step.step_id: int(step.worst_case_executions) for step in self.steps}

    @property
    def definition_payload(self) -> dict[str, Any]:
        """Return a fresh copy of the canonical definition payload."""
        payload = json.loads(self.definition_json)
        if not isinstance(payload, dict):  # pragma: no cover - built internally
            raise RecipeCompileError("compiled definition is not a JSON object")
        return payload

    def to_dict(self) -> dict[str, Any]:
        """Return the definition plus its two identity digests."""
        payload = self.definition_payload
        payload["plan_definition_digest"] = self.plan_definition_digest
        payload["schema_digest"] = self.schema_digest
        return payload

    def to_json(self) -> str:
        """Return the canonical JSON encoding of the compiled definition."""
        return canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class StepExecutionIdentity:
    """Dispatch-time science identity for exactly one compiled step."""

    step_id: str
    plan_definition_digest: str
    execution_identity: str
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Return the identity payload plus its ``v2:`` fingerprint."""
        payload = copy.deepcopy(dict(self.payload))
        payload["execution_identity"] = self.execution_identity
        return payload

    def to_json(self) -> str:
        """Return the canonical JSON encoding of the step identity."""
        return canonical_json(self.to_dict())


@dataclass(frozen=True, slots=True)
class HistoricalRecipe:
    """Read-only historical recipe: unknown fields kept, never executable."""

    payload: Mapping[str, Any]
    unknown_fields: tuple[str, ...]
    execution_eligible: bool = False
    recovery_eligible: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the preserved payload with the explicit eligibility flags."""
        return {
            "payload": copy.deepcopy(dict(self.payload)),
            "unknown_fields": list(self.unknown_fields),
            "execution_eligible": self.execution_eligible,
            "recovery_eligible": self.recovery_eligible,
        }


# ---------------------------------------------------------------------------
# Step compilation
# ---------------------------------------------------------------------------


def _request_view(request: TaskRequest) -> dict[str, Any]:
    """Return a path-free definition projection of the authoritative request."""
    payload: dict[str, Any] = {
        "schema_version": request.schema_version,
        "task": request.task.value,
        "charge": request.charge,
        "multiplicity": request.multiplicity,
        "level": request.level.to_dict(),
        "options": options_to_dict(request.options) or {},
    }
    structure = _structure_identity_view(request.structure)
    if structure is not None:
        payload["structure"] = structure
    if request.backend is not None:
        payload["backend"] = request.backend
    if request.electronic_state is not None:
        payload["electronic_state"] = electronic_state_spec_to_dict(request.electronic_state)
    return payload


def _validated_options(
    task_kind: str,
    inputs: RecipeInputs,
    raw_parameters: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return closed, typed step parameters mapped onto the task options."""
    parameters = {str(key): value for key, value in (raw_parameters or {}).items()}
    allowed = _option_field_names(task_kind)
    for key in parameters:
        if key not in allowed:
            raise UnknownScientificFieldError(
                key,
                where=f"step[{task_kind}].parameters",
                expected=allowed,
            )
    if (
        inputs.standard_state is not None
        and "standard_state" in allowed
        and "standard_state" not in parameters
    ):
        parameters["standard_state"] = inputs.standard_state
    return {key: parameters[key] for key in sorted(parameters)}


def _build_task_request(
    task_kind: str,
    inputs: RecipeInputs,
    raw_parameters: Mapping[str, Any] | None,
) -> TaskRequest:
    """Build the definition-time ``TaskRequest`` for one step.

    Upstream products are not materialized here; the request carries the
    initial science inputs and the declared parameters.  B18 materializes
    upstream artifacts and revalidates at dispatch.
    """
    options_payload = _validated_options(task_kind, inputs, raw_parameters)
    options_cls = TASK_OPTIONS_TYPES[TaskKind(task_kind)]
    options: TaskOptions | None
    try:
        options = options_cls.from_dict(options_payload)  # type: ignore[attr-defined]
    except (TaskInputError, ValueError, TypeError) as exc:
        raise CompileInputError(
            f"step {task_kind!r} parameters are invalid: {exc}",
            detail={"field": "step.parameters", "actual": dict(options_payload)},
        ) from exc
    structure = None if task_kind == TaskKind.THERMOCHEMISTRY.value else inputs.structure
    return TaskRequest(
        task=TaskKind(task_kind),
        structure=structure,
        charge=inputs.charge,
        multiplicity=inputs.multiplicity,
        level=inputs.method,
        backend=inputs.backend,
        electronic_state=inputs.electronic_state,
        options=options,
    )


def _compile_step(
    step: Step,
    inputs: RecipeInputs,
    registry: CapabilityRegistry,
) -> CompiledStep:
    """Bind one validated recipe step to its single existing output."""
    parameters = dict(step.parameters or {})
    if step.capability_ref is not None:
        descriptor = registry.require(step.capability_ref).descriptor
        worst_case = int(descriptor.execution_semantics.worst_case_executions)
        if descriptor.kind is CapabilityKind.PURE_ANALYSIS:
            binding: TaskBinding = bind_capability(step.capability_ref, registry=registry)
            return CompiledStep(
                recipe_step=step,
                selection=binding.selection,
                task_kind=None,
                binding_id=binding.binding_id,
                delegation_target=binding.delegation_target,
                dispatchable=binding.dispatchable,
                implementation_id=binding.implementation_id,
                request=None,
                request_view=None,
                parameters=_freeze_sorted_mapping(parameters),
                worst_case_executions=0,
            )
        task_kind = descriptor.delegates_to_task_kind
        if task_kind is None:  # pragma: no cover - descriptor contract forbids it
            raise CompileInputError(
                f"capability {step.capability_ref!r} has no task-kind delegation",
                detail={"field": "step.capability_ref", "actual": step.capability_ref},
            )
        request = _build_task_request(task_kind, inputs, step.parameters)
        binding = bind_capability(step.capability_ref, request, registry=registry)
    else:
        task_kind = str(step.task_kind)
        worst_case = 1
        request = _build_task_request(task_kind, inputs, step.parameters)
        binding = bind_task_kind(task_kind, request)
    return CompiledStep(
        recipe_step=step,
        selection=binding.selection,
        task_kind=task_kind,
        binding_id=binding.binding_id,
        delegation_target=binding.delegation_target,
        dispatchable=binding.dispatchable,
        implementation_id=binding.implementation_id,
        request=request,
        request_view=_request_view(request),
        parameters=_freeze_sorted_mapping(parameters),
        worst_case_executions=worst_case if binding.dispatchable else 0,
    )


# ---------------------------------------------------------------------------
# Definition payload
# ---------------------------------------------------------------------------


def _sorted_edge_payloads(recipe: Recipe) -> list[dict[str, Any]]:
    edges = sorted(recipe.edges, key=lambda edge: edge.edge_id)
    return [copy.deepcopy(edge.to_dict()) for edge in edges]


def _sorted_alternative_payloads(recipe: Recipe) -> list[dict[str, Any]]:
    alternatives = sorted(recipe.alternatives, key=lambda item: item.alternative_id)
    return [copy.deepcopy(item.to_dict()) for item in alternatives]


def _sorted_validator_payloads(recipe: Recipe) -> list[dict[str, Any]]:
    validators = sorted(
        recipe.validators,
        key=lambda item: (item.validator_id, item.validator_version),
    )
    return [validator.to_dict() for validator in validators]


def _definition_payload(
    recipe: Recipe,
    inputs: RecipeInputs,
    steps: Sequence[CompiledStep],
    report: RecipeValidationReport,
) -> dict[str, Any]:
    return {
        "schema": COMPILED_RECIPE_SCHEMA,
        "schema_version": COMPILED_RECIPE_SCHEMA_VERSION,
        "recipe": {
            "recipe_id": recipe.recipe_id,
            "schema_version": recipe.schema_version,
            "applicability": recipe.applicability.to_dict(),
            "limits": recipe.limits.to_dict(),
            "validators": _sorted_validator_payloads(recipe),
        },
        "inputs": inputs.identity_view(),
        "topological_order": list(report.topological_order),
        "steps": sorted(
            (step.to_dict() for step in steps),
            key=lambda item: str(item["step_id"]),
        ),
        "edges": _sorted_edge_payloads(recipe),
        "alternatives": _sorted_alternative_payloads(recipe),
    }


# ---------------------------------------------------------------------------
# Public compile API
# ---------------------------------------------------------------------------


def compile_recipe(
    recipe: Recipe | Mapping[str, Any],
    inputs: RecipeInputs | Mapping[str, Any] | None = None,
    *,
    approval: ApprovalMetadata | Mapping[str, Any] | None = None,
    registry: CapabilityRegistry | None = None,
) -> CompiledRecipe:
    """Compile a validated recipe into a deterministic ``TaskRequest`` DAG.

    Args:
        recipe: A :class:`Recipe` or its canonical v1 mapping.  Mapping
            payloads are closed: an unknown scientific field is rejected here
            and is never silently ignored for execution.
        inputs: Frozen initial science inputs.  ``None`` compiles the
            definition with empty inputs; dispatch remains responsible for
            supplying the structure a task needs.
        approval: Optional management metadata.  It is stored on the result
            and is deliberately excluded from both identities.
        registry: Optional capability registry; defaults to the built-in
            catalog.

    Returns:
        The compiled definition with canonical JSON and both identity digests.

    Raises:
        RecipeSchemaError: The mapping is not a closed canonical v1 recipe.
        RecipeCycleError, DanglingReferenceError, DuplicateStepError,
        MissingRequiredStepError, DiagnosticDependencyError,
        UnknownRecipeCapabilityError, UnknownRecipeTaskKindError,
        RecipeLimitError: The recipe DAG is invalid (see ``validate_recipe``).
        UnknownScientificFieldError: Initial inputs or a step parameter carry
            a field the execution contract does not understand.
        CompileInputError: An input value violates its typed contract.
        UnknownCapabilityError: A capability is not registered.
    """
    contract = recipe if isinstance(recipe, Recipe) else Recipe.from_dict(recipe)
    typed_inputs = RecipeInputs.from_value(inputs)
    typed_approval = ApprovalMetadata.from_value(approval)
    active_registry = registry if registry is not None else builtin_registry()
    report = validate_recipe(contract, registry=active_registry)

    by_id = {step.step_id: step for step in contract.steps}
    compiled_steps = tuple(
        _compile_step(by_id[step_id], typed_inputs, active_registry)
        for step_id in report.topological_order
    )
    definition_payload = _definition_payload(contract, typed_inputs, compiled_steps, report)
    definition_json = canonical_json(definition_payload)
    plan_definition_digest = identity_fingerprint(definition_payload)
    schema_digest = "sha256:" + hashlib.sha256(definition_json.encode("utf-8")).hexdigest()
    logger.debug(
        "compiled recipe %s: %d steps, definition %s",
        contract.recipe_id,
        len(compiled_steps),
        plan_definition_digest,
    )
    return CompiledRecipe(
        recipe_id=contract.recipe_id,
        schema_version=contract.schema_version,
        recipe=contract,
        inputs=typed_inputs,
        steps=compiled_steps,
        edges=contract.edges,
        alternatives=contract.alternatives,
        topological_order=report.topological_order,
        validation=report,
        definition_json=definition_json,
        plan_definition_digest=plan_definition_digest,
        schema_digest=schema_digest,
        approval=typed_approval,
    )


# ---------------------------------------------------------------------------
# OutputRef materialization at dispatch
# ---------------------------------------------------------------------------


def output_ref_address(reference: OutputRef) -> str:
    """Return the stable address used to look up an upstream content digest.

    The address is ``<from_step>::<product_kind>::<canonical selector>`` and
    contains no machine path, job id or display name.
    """
    selector = json.dumps(
        reference.selector.to_dict(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"{reference.from_step}::{reference.product_kind}::{selector}"


def _resolve_selector(reference: OutputRef, step_id: str) -> dict[str, Any]:
    """Return the canonical selector or raise for an unresolved one."""
    selector = reference.selector
    kind = selector.selector_kind
    has_index = selector.index is not None
    has_range = selector.range is not None
    has_field = selector.field is not None
    if kind == "whole_product":
        resolved = not (has_index or has_range or has_field)
    elif kind == "index":
        resolved = has_index and not has_range and not has_field
    elif kind == "slice":
        resolved = (
            has_range
            and not has_index
            and not has_field
            and selector.range is not None
            and selector.range[0] < selector.range[1]
        )
    elif kind == "field":
        resolved = has_field and not has_index and not has_range
    else:  # conformer | mode | frame
        resolved = (has_index or has_field) and not has_range
    if not resolved:
        raise UnresolvedOutputRefError(
            step_id=step_id,
            reference=reference,
            reason="unresolved_output_ref_selector",
            expected=f"a concrete selector for kind {kind!r}",
            actual=selector.to_dict(),
        )
    return selector.to_dict()


def _dependency_references(compiled: CompiledRecipe, step: CompiledStep) -> tuple[OutputRef, ...]:
    """Return every typed dependency of *step* (consumes plus edge refs).

    An edge that carries an ``OutputRef`` adds that typed product.  A bare
    structural edge adds the upstream step's whole-result digest only when no
    ``consumes`` entry already selects a product from the same upstream; the
    explicit selector is always the more precise dependency.
    """
    references: list[OutputRef] = list(step.recipe_step.consumes)
    selected_steps = {reference.from_step for reference in references}
    for edge in compiled.edges:
        if edge.to_step != step.step_id:
            continue
        if edge.output_ref is not None:
            references.append(edge.output_ref)
        elif edge.from_step not in selected_steps:
            references.append(
                OutputRef(
                    from_step=edge.from_step,
                    product_kind=WHOLE_STEP_PRODUCT_KIND,
                    selector=Selector(selector_kind="whole_product"),
                )
            )
    unique: dict[str, OutputRef] = {}
    for reference in references:
        unique.setdefault(output_ref_address(reference), reference)
    return tuple(unique[address] for address in sorted(unique))


def _normalize_upstream_digest(value: object, *, address: str) -> str:
    if not isinstance(value, str):
        raise InvalidUpstreamDigestError(address=address, actual=type(value).__name__)
    text = value.strip().lower()
    hex_part = text[len("sha256:") :] if text.startswith("sha256:") else text
    if not _SHA256_RE.fullmatch(hex_part):
        raise InvalidUpstreamDigestError(address=address, actual=value)
    return f"sha256:{hex_part}"


def _normalize_provider_versions(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise InvalidProviderVersionError(actual=type(value).__name__)
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not key or not isinstance(raw_value, str) or not raw_value:
            raise InvalidProviderVersionError(actual={key: raw_value})
        normalized[key] = raw_value
    return {key: normalized[key] for key in sorted(normalized)}


def _json_safe(value: object, where: str) -> Any:
    """Return a deep JSON-safe copy; a path is a typed identity error."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        raise StepIdentityError(
            f"{where}: file locations never enter a science identity; "
            "supply the content digest instead",
            detail={
                "field": where,
                "reason": "path_in_identity",
                "expected": "content digest",
                "actual": str(value),
            },
        )
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, f"{where}.{key}")
            for key, item in sorted(value.items(), key=lambda kv: str(kv[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, f"{where}[]") for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value), where)
    raise StepIdentityError(
        f"{where}: value of type {type(value).__name__} is not identity-safe",
        detail={
            "field": where,
            "reason": "non_json_identity_value",
            "expected": "JSON scalar/list/mapping",
            "actual": type(value).__name__,
        },
    )


def materialize_step_identity(
    compiled: CompiledRecipe,
    step_id: str,
    upstream_digests: Mapping[str, str],
    provider_versions: Mapping[str, str],
    *,
    materialized_parameters: Mapping[str, Any] | None = None,
) -> StepExecutionIdentity:
    """Materialize one step's science identity from actual dispatch inputs.

    Args:
        compiled: The compiled definition produced by :func:`compile_recipe`.
        step_id: The step whose execution identity is being frozen.
        upstream_digests: Map from :func:`output_ref_address` to the actual
            SHA-256 content digest of the selected upstream product.
        provider_versions: Provider/plugin versions present at dispatch.
        materialized_parameters: Optional dispatch-time parameters that
            replace the definition-time options (for example a resolved
            upstream artifact).  Paths are rejected; supply a content digest.

    Returns:
        The step execution identity with its ``v2:`` fingerprint.

    Raises:
        UnknownCompiledStepError: *step_id* is not in the definition.
        UnresolvedOutputRefError: A selector is unresolved or its upstream
            digest is missing; the step is never silently skipped.
        InvalidUpstreamDigestError: A supplied digest is not SHA-256.
        InvalidProviderVersionError: Provider versions are malformed.
    """
    step = compiled.step(step_id)
    dependencies: list[dict[str, Any]] = []
    for reference in _dependency_references(compiled, step):
        selector = _resolve_selector(reference, step_id)
        address = output_ref_address(reference)
        if address not in upstream_digests:
            raise UnresolvedOutputRefError(
                step_id=step_id,
                reference=reference,
                reason="missing_upstream_digest",
                expected="a content digest for the selected upstream product",
                actual=None,
            )
        dependencies.append(
            {
                "address": address,
                "from_step": reference.from_step,
                "product_kind": reference.product_kind,
                "selector": selector,
                "digest": _normalize_upstream_digest(
                    upstream_digests[address],
                    address=address,
                ),
            }
        )
    versions = _normalize_provider_versions(provider_versions)
    if materialized_parameters is None:
        parameters = (
            dict(step.parameters)
            if step.request is None
            else options_to_dict(step.request.options) or {}
        )
    else:
        parameters = _json_safe(materialized_parameters, "materialized_parameters")
    implementation_version = versions.get(step.implementation_id)
    payload: dict[str, Any] = {
        "identity_schema": IDENTITY_SCHEMA,
        "scope": "step_execution",
        "recipe_id": compiled.recipe_id,
        "plan_definition_digest": compiled.plan_definition_digest,
        "step_id": step.step_id,
        "task_kind": step.task_kind,
        "selection": step.selection.to_dict(),
        "binding_id": step.binding_id,
        "implementation_id": step.implementation_id,
        "science": {
            "inputs": compiled.inputs.identity_view(provider_versions=versions),
            "parameters": parameters,
            "dependencies": dependencies,
            "provider_versions": versions,
        },
    }
    if implementation_version is not None:
        payload["implementation_version"] = implementation_version
    return StepExecutionIdentity(
        step_id=step.step_id,
        plan_definition_digest=compiled.plan_definition_digest,
        execution_identity=identity_fingerprint(payload),
        payload=MappingProxyType(payload),
    )


# ---------------------------------------------------------------------------
# Historical read
# ---------------------------------------------------------------------------


def _known(object_type: type) -> tuple[str, ...]:
    return tuple(field.name for field in fields(object_type))


def _collect_unknown(
    payload: Any,
    object_type: type,
    where: str,
    unknown: list[str],
) -> None:
    if not isinstance(payload, Mapping):
        return
    allowed = _known(object_type)
    for key, value in payload.items():
        path = f"{where}.{key}" if where else str(key)
        if str(key) not in allowed:
            unknown.append(path)
        if str(key) == "steps" and isinstance(value, Sequence) and not isinstance(value, str):
            for index, item in enumerate(value):
                _collect_unknown(item, Step, f"{path}[{index}]", unknown)
        elif str(key) == "edges" and isinstance(value, Sequence) and not isinstance(value, str):
            for index, item in enumerate(value):
                _collect_unknown(item, Edge, f"{path}[{index}]", unknown)
        elif (
            str(key) == "alternatives"
            and isinstance(value, Sequence)
            and not isinstance(value, str)
        ):
            for index, item in enumerate(value):
                _collect_unknown(item, Alternative, f"{path}[{index}]", unknown)
        elif (
            str(key) == "validators" and isinstance(value, Sequence) and not isinstance(value, str)
        ):
            for index, item in enumerate(value):
                _collect_unknown(item, ValidatorRef, f"{path}[{index}]", unknown)


def read_historical_recipe(payload: Mapping[str, Any]) -> HistoricalRecipe:
    """Preserve a historical recipe payload without granting eligibility.

    Unknown fields — top-level or nested — are kept verbatim.  The result is
    hard-coded non-executable and non-recoverable: tolerating an unknown field
    for reading never makes it valid science for a new run.

    Args:
        payload: Any historical recipe mapping.

    Returns:
        The preserved payload, the unknown field paths and the eligibility
        flags.

    Raises:
        RecipeCompileError: *payload* is not a mapping.
    """
    if not isinstance(payload, Mapping):
        raise RecipeCompileError(
            "historical recipe payload must be a mapping",
            detail={"reason": "historical_payload_not_mapping"},
        )
    preserved = copy.deepcopy(dict(payload))
    unknown: list[str] = []
    allowed = set(_known(Recipe))
    for key in preserved:
        if str(key) not in allowed:
            unknown.append(str(key))
    for index, item in enumerate(preserved.get("steps", ())):
        _collect_unknown(item, Step, f"steps[{index}]", unknown)
    for index, item in enumerate(preserved.get("edges", ())):
        _collect_unknown(item, Edge, f"edges[{index}]", unknown)
    for index, item in enumerate(preserved.get("alternatives", ())):
        _collect_unknown(item, Alternative, f"alternatives[{index}]", unknown)
    for index, item in enumerate(preserved.get("validators", ())):
        _collect_unknown(item, ValidatorRef, f"validators[{index}]", unknown)
    return HistoricalRecipe(
        payload=MappingProxyType(preserved),
        unknown_fields=tuple(sorted(unknown)),
        execution_eligible=False,
        recovery_eligible=False,
    )
