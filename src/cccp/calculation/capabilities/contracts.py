"""Frozen scientific capability descriptor contracts (ACP2 science A-05a).

The types here mirror ``docs/reference/next-change/acp2-contracts/science/
capability.schema.json`` (``acp2.science.capability`` schema version 1).  A
capability is a *scientific* statement of what can be computed; the
implementation is the existing CCCP task/analysis seam that carries it out.
The two are deliberately separate fields.

The four state projections (``described``, ``installed``, ``available`` and
``verified``) are modelled explicitly and validated independently:

* ``described`` is always ``present`` for a registered descriptor;
* ``installed`` and ``available`` are environment observations, never
  scientific evidence;
* ``verified`` may only be supplied by evidence-bearing metadata, never
  inferred from installation or availability.

The module is dependency-free apart from the neutral calculation error base:
importing it pulls in no task execution module, no QC interface and never
starts a subprocess.

Author: QCcalc Team
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType

from cccp.calculation.errors import CalculationError

__all__ = [
    "BACKWARD_COMPATIBLE_ADDITIONS",
    "CAPABILITY_CONTRACT_VERSION",
    "CAPABILITY_SCHEMA",
    "CAPABILITY_SCHEMA_VERSION",
    "CAPABILITY_STATE_RULES",
    "COMPLETION_FAILURE_ERROR_CODES",
    "CONTRACT_VERSION",
    "ERROR_CODES",
    "HISTORICAL_UNKNOWN_FIELDS_POLICY",
    "SUPPORTED_MAJOR_VERSIONS",
    "TASK_KINDS",
    "UNKNOWN_FIELD_HISTORICAL_READ_POLICY",
    "UNKNOWN_MAJOR_VERSION_POLICY",
    "CapabilityContractError",
    "CapabilityDescriptor",
    "CapabilityKind",
    "CapabilityProjections",
    "CapabilityState",
    "CapabilityStateError",
    "Compatibility",
    "CompletionCheck",
    "DuplicateCapabilityError",
    "DuplicateImplementationError",
    "EvaluatedBy",
    "ExecutionSemantics",
    "IncompatibleStateError",
    "Limitation",
    "LimitationSeverity",
    "Precondition",
    "PreconditionKind",
    "Projection",
    "ProvenanceRequirements",
    "SchemaReference",
    "SideEffects",
    "UnknownCapabilityError",
    "UnknownImplementationError",
    "UnknownMajorVersionError",
    "UnknownFieldPolicy",
    "ValidationLayer",
    "ValidationLayers",
]

#: Contract identity frozen by the ACP2 science capability schema.
CAPABILITY_SCHEMA = "acp2.science.capability"
CAPABILITY_SCHEMA_VERSION = 1
CAPABILITY_CONTRACT_VERSION = "acp2.science.capability.v1"
CONTRACT_VERSION = CAPABILITY_CONTRACT_VERSION

SUPPORTED_MAJOR_VERSIONS: tuple[int, ...] = (1,)
UNKNOWN_MAJOR_VERSION_POLICY = "reject_and_never_execute"
HISTORICAL_UNKNOWN_FIELDS_POLICY = "preserve_read_only_no_execution_or_recovery_eligibility"
#: The ``unknown_field_policy.historical_read`` spelling is deliberately a
#: distinct frozen constant: ``capability.schema.json`` uses a different
#: const string for that object than for ``compatibility``.  Both mean the
#: same thing (preserve historical payloads, grant no eligibility), but the
#: wire values must match the schema exactly.
UNKNOWN_FIELD_HISTORICAL_READ_POLICY = (
    "preserve_unknown_fields_without_execution_or_recovery_eligibility"
)
BACKWARD_COMPATIBLE_ADDITIONS = "same_major_only"

#: The frozen six-code error vocabulary shared with ``errors.schema.json``.
ERROR_CODES: tuple[str, ...] = (
    "unknown_major_version",
    "duplicate_capability_id",
    "unknown_implementation",
    "disabled_provider",
    "unavailable_binary",
    "invalid_scientific_input",
)
COMPLETION_FAILURE_ERROR_CODES = ERROR_CODES

#: The fifteen existing CCCP task kinds a compute capability may delegate to.
TASK_KINDS: tuple[str, ...] = (
    "singlepoint",
    "optimize",
    "frequency",
    "scan",
    "irc",
    "casscf",
    "thermochemistry",
    "conformer_search",
    "md_sampling",
    "clustering",
    "censo_refine",
    "nmr_shielding",
    "xtb_path_search",
    "orca_gradient",
    "xtb_constrained_opt",
)

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPABILITY_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_SCHEMA_REF_RE = re.compile(
    r"^(https://acp\.local/acp2/science/.+\.schema\.json(#.+)?"
    r"|cccp\.[a-z0-9_.]+#[A-Za-z0-9_]+)$"
)
_EVALUATOR_RE = re.compile(r"^cccp\.[a-z0-9_.]+$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class CapabilityContractError(CalculationError):
    """Base class for capability-contract violations."""


class UnknownCapabilityError(CapabilityContractError, LookupError):
    """A capability id is not present in this registry."""


class UnknownImplementationError(CapabilityContractError, LookupError):
    """An implementation id is not present in this registry."""


class DuplicateCapabilityError(CapabilityContractError, ValueError):
    """Two descriptors declare the same capability id."""


class DuplicateImplementationError(CapabilityContractError, ValueError):
    """Two descriptors declare the same implementation id."""


class CapabilityStateError(CapabilityContractError, ValueError):
    """A projection/execution state is unknown or internally incompatible."""


class IncompatibleStateError(CapabilityStateError):
    """Two explicit states contradict each other.

    For example a ``compute`` execution semantics record that claims it
    starts no subprocess, or a ``verified`` projection without evidence.
    """


class UnknownMajorVersionError(CapabilityStateError):
    """A contract major version other than v1 is rejected, never executed."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CapabilityKind(str, Enum):
    """Execution semantics of a capability."""

    COMPUTE = "compute"
    PURE_ANALYSIS = "pure_analysis"


class CapabilityState(str, Enum):
    """State value shared by the four independent projections."""

    UNKNOWN = "unknown"
    PRESENT = "present"
    ABSENT = "absent"
    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"


class PreconditionKind(str, Enum):
    """Declared precondition families from the frozen schema."""

    SOFTWARE = "software"
    BINARY = "binary"
    LICENSE = "license"
    INPUT = "input"
    ARTIFACT = "artifact"
    SCIENTIFIC = "scientific"
    ENVIRONMENT = "environment"


class EvaluatedBy(str, Enum):
    """Which validation layer evaluates a precondition."""

    CONTRACT = "contract"
    CCCP = "cccp"
    RUNTIME = "runtime"


class LimitationSeverity(str, Enum):
    """Severity vocabulary for declared limitations."""

    INFO = "info"
    WARNING = "warning"
    HARD_LIMIT = "hard_limit"


class SideEffects(str, Enum):
    """Side-effect vocabulary for execution semantics."""

    NONE = "none"
    ARTIFACTS_ONLY = "artifacts_only"


#: Allowed state values per projection, frozen by ``capability.schema.json``.
CAPABILITY_STATE_RULES: Mapping[str, frozenset[CapabilityState]] = MappingProxyType(
    {
        "described": frozenset({CapabilityState.PRESENT}),
        "installed": frozenset(
            {
                CapabilityState.UNKNOWN,
                CapabilityState.PRESENT,
                CapabilityState.ABSENT,
                CapabilityState.NOT_VERIFIED,
            }
        ),
        "available": frozenset(
            {
                CapabilityState.UNKNOWN,
                CapabilityState.PRESENT,
                CapabilityState.ABSENT,
                CapabilityState.NOT_VERIFIED,
            }
        ),
        "verified": frozenset(
            {
                CapabilityState.UNKNOWN,
                CapabilityState.NOT_VERIFIED,
                CapabilityState.VERIFIED,
            }
        ),
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise CapabilityContractError(f"{field_name} must match ^[a-z][a-z0-9_]*$ (got {value!r})")
    return value


def _require_capability_identifier(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not _CAPABILITY_ID_RE.fullmatch(value):
        raise CapabilityContractError(
            f"{field_name} must be a dotted identifier "
            f"(^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$; got {value!r})"
        )
    return value


def _require_error_code(value: str, *, field_name: str) -> str:
    if value not in ERROR_CODES:
        raise CapabilityStateError(
            f"{field_name} {value!r} is not in the frozen error vocabulary "
            f"({', '.join(ERROR_CODES)})"
        )
    return value


def _coerce_enum(enum_cls: type[Enum], value: object, *, field_name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = ", ".join(str(item.value) for item in enum_cls)
        raise CapabilityStateError(
            f"{field_name} {value!r} is unknown; expected one of: {allowed}"
        ) from exc


# ---------------------------------------------------------------------------
# Descriptor value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SchemaReference:
    """Reference to a science JSON Schema or an existing CCCP contract."""

    ref: str
    schema_version: int = 1
    digest: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str) or not _SCHEMA_REF_RE.fullmatch(self.ref):
            raise CapabilityContractError(
                f"schema ref {self.ref!r} must be an acp2 science schema URL "
                "or a cccp.<dotted>#<Type> contract identifier"
            )
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise CapabilityStateError("schema_version must be an integer >= 1")
        if self.digest is not None and not _DIGEST_RE.fullmatch(self.digest):
            raise CapabilityStateError(
                "digest must be 'sha256:' followed by 64 lowercase hex characters"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "ref": self.ref,
            "schema_version": self.schema_version,
        }
        if self.digest is not None:
            payload["digest"] = self.digest
        return payload


@dataclass(frozen=True)
class Precondition:
    """A declared precondition.

    For ``kind`` of ``software`` or ``binary`` the ``id`` is the
    ``cccp.software`` executable key (for example ``orca``).  That is the
    only convention the read-only registry uses to derive the installed /
    available projections; no second resolver exists.
    """

    id: str
    kind: PreconditionKind | str
    description: str
    required: bool
    error_code: str
    evaluated_by: EvaluatedBy | str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, field_name="precondition.id")
        object.__setattr__(
            self, "kind", _coerce_enum(PreconditionKind, self.kind, field_name="precondition.kind")
        )
        if not isinstance(self.description, str) or not self.description.strip():
            raise CapabilityContractError("precondition.description must be non-empty")
        if not isinstance(self.required, bool):
            raise CapabilityContractError("precondition.required must be a boolean")
        _require_error_code(self.error_code, field_name="precondition.error_code")
        if self.evaluated_by is not None:
            object.__setattr__(
                self,
                "evaluated_by",
                _coerce_enum(
                    EvaluatedBy, self.evaluated_by, field_name="precondition.evaluated_by"
                ),
            )

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "id": self.id,
            "kind": self.kind.value,
            "description": self.description,
            "required": self.required,
            "error_code": self.error_code,
        }
        if self.evaluated_by is not None:
            payload["evaluated_by"] = self.evaluated_by.value
        return payload


@dataclass(frozen=True)
class CompletionCheck:
    """A declared scientific completion check."""

    id: str
    description: str
    evaluator: str
    required: bool
    failure_error_code: str
    compares: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, field_name="completion_check.id")
        if not isinstance(self.description, str) or not self.description.strip():
            raise CapabilityContractError("completion_check.description must be non-empty")
        if not isinstance(self.evaluator, str) or not _EVALUATOR_RE.fullmatch(self.evaluator):
            raise CapabilityContractError(
                "completion_check.evaluator must match ^cccp\\.[a-z0-9_.]+$"
            )
        if not isinstance(self.required, bool):
            raise CapabilityContractError("completion_check.required must be a boolean")
        _require_error_code(
            self.failure_error_code, field_name="completion_check.failure_error_code"
        )
        if self.compares is not None:
            object.__setattr__(self, "compares", tuple(self.compares))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "id": self.id,
            "description": self.description,
            "evaluator": self.evaluator,
            "required": self.required,
            "failure_error_code": self.failure_error_code,
        }
        if self.compares is not None:
            payload["compares"] = list(self.compares)
        return payload


@dataclass(frozen=True)
class Limitation:
    """A declared capability limitation."""

    id: str
    text: str
    severity: LimitationSeverity | str
    applies_to: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.id, field_name="limitation.id")
        if not isinstance(self.text, str) or not self.text.strip():
            raise CapabilityContractError("limitation.text must be non-empty")
        object.__setattr__(
            self,
            "severity",
            _coerce_enum(LimitationSeverity, self.severity, field_name="limitation.severity"),
        )
        if self.applies_to is not None and (
            not isinstance(self.applies_to, str) or not self.applies_to.strip()
        ):
            raise CapabilityContractError("limitation.applies_to must be null or non-empty")

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "id": self.id,
            "text": self.text,
            "severity": self.severity.value,
        }
        if self.applies_to is not None:
            payload["applies_to"] = self.applies_to
        return payload


@dataclass(frozen=True)
class Compatibility:
    """Frozen capability contract compatibility declaration."""

    contract_version: str = CAPABILITY_CONTRACT_VERSION
    declared_major: int = 1
    min_reader_schema_version: int = 1
    max_reader_schema_version: int = 1
    unknown_major_version: str = UNKNOWN_MAJOR_VERSION_POLICY
    unknown_major_error_code: str = "unknown_major_version"
    historical_unknown_fields: str = HISTORICAL_UNKNOWN_FIELDS_POLICY
    backward_compatible_additions: str = BACKWARD_COMPATIBLE_ADDITIONS

    def __post_init__(self) -> None:
        if self.contract_version != CAPABILITY_CONTRACT_VERSION:
            raise IncompatibleStateError(
                f"compatibility.contract_version must be {CAPABILITY_CONTRACT_VERSION!r}"
            )
        if self.declared_major not in SUPPORTED_MAJOR_VERSIONS:
            raise UnknownMajorVersionError(
                f"declared major {self.declared_major!r} is not supported; "
                f"supported: {list(SUPPORTED_MAJOR_VERSIONS)}"
            )
        if (
            not isinstance(self.min_reader_schema_version, int)
            or not isinstance(self.max_reader_schema_version, int)
            or self.min_reader_schema_version < 1
            or self.max_reader_schema_version < self.min_reader_schema_version
        ):
            raise IncompatibleStateError("reader schema version range must be positive and ordered")
        if self.declared_major not in range(
            self.min_reader_schema_version, self.max_reader_schema_version + 1
        ):
            raise IncompatibleStateError(
                "declared_major must lie inside the declared reader schema range"
            )
        if self.unknown_major_version != UNKNOWN_MAJOR_VERSION_POLICY:
            raise IncompatibleStateError("unknown_major_version must be 'reject_and_never_execute'")
        _require_error_code(
            self.unknown_major_error_code, field_name="compatibility.unknown_major_error_code"
        )
        if self.unknown_major_error_code != "unknown_major_version":
            raise IncompatibleStateError(
                "compatibility.unknown_major_error_code must be 'unknown_major_version'"
            )
        if self.historical_unknown_fields != HISTORICAL_UNKNOWN_FIELDS_POLICY:
            raise IncompatibleStateError(
                "historical_unknown_fields policy is frozen for schema version 1"
            )
        if self.backward_compatible_additions != BACKWARD_COMPATIBLE_ADDITIONS:
            raise IncompatibleStateError("backward_compatible_additions must be 'same_major_only'")

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "contract_version": self.contract_version,
            "declared_major": self.declared_major,
            "min_reader_schema_version": self.min_reader_schema_version,
            "max_reader_schema_version": self.max_reader_schema_version,
            "unknown_major_version": self.unknown_major_version,
            "unknown_major_error_code": self.unknown_major_error_code,
            "historical_unknown_fields": self.historical_unknown_fields,
            "backward_compatible_additions": self.backward_compatible_additions,
        }


@dataclass(frozen=True)
class ProvenanceRequirements:
    """What a capability result must carry to be scientifically traceable."""

    software_version: str = "required"
    method_identity: str = "required"
    input_digest: str = "required"
    units: str = "required"
    artifact_digests: str = "required"
    notes: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "software_version",
            "input_digest",
            "units",
            "artifact_digests",
        ):
            if getattr(self, field_name) != "required":
                raise IncompatibleStateError(f"provenance_requirements.{field_name} is frozen")
        if self.method_identity not in ("required", "not_applicable"):
            raise CapabilityStateError(
                "provenance_requirements.method_identity must be 'required' or 'not_applicable'"
            )
        if self.notes is not None:
            object.__setattr__(self, "notes", tuple(self.notes))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {
            "software_version": self.software_version,
            "method_identity": self.method_identity,
            "input_digest": self.input_digest,
            "units": self.units,
            "artifact_digests": self.artifact_digests,
        }
        if self.notes is not None:
            payload["notes"] = list(self.notes)
        return payload


@dataclass(frozen=True)
class ExecutionSemantics:
    """Compute versus pure-analysis execution contract.

    A ``compute`` capability delegates to exactly one existing task kind and
    starts a subprocess.  A ``pure_analysis`` capability reads existing
    artifacts only, has no side effects and never starts a calculation.
    """

    kind: CapabilityKind | str
    starts_subprocess: bool
    reads_existing_artifacts_only: bool
    delegates_to_task_kind: str | None
    delegation_count: int = 0
    side_effects: SideEffects | str = SideEffects.NONE
    network_access: bool = False
    writes_artifacts: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "kind", _coerce_enum(CapabilityKind, self.kind, field_name="execution.kind")
        )
        object.__setattr__(
            self,
            "side_effects",
            _coerce_enum(SideEffects, self.side_effects, field_name="execution.side_effects"),
        )
        if self.kind is CapabilityKind.COMPUTE:
            if self.starts_subprocess is not True:
                raise IncompatibleStateError("compute execution must start a subprocess")
            if self.reads_existing_artifacts_only is not False:
                raise IncompatibleStateError("compute execution may not be artifact-read-only")
            if self.delegates_to_task_kind not in TASK_KINDS:
                raise CapabilityStateError(
                    "compute execution must delegate to exactly one existing "
                    f"task kind ({', '.join(TASK_KINDS)})"
                )
            if self.delegation_count != 1:
                raise IncompatibleStateError("compute execution delegation_count must be 1")
            if self.side_effects is not SideEffects.ARTIFACTS_ONLY:
                raise IncompatibleStateError(
                    "compute execution side_effects must be 'artifacts_only'"
                )
            if self.writes_artifacts is not True:
                raise IncompatibleStateError("compute execution must write artifacts")
        else:
            if self.starts_subprocess is not False:
                raise IncompatibleStateError("pure_analysis execution must not start a subprocess")
            if self.reads_existing_artifacts_only is not True:
                raise IncompatibleStateError(
                    "pure_analysis execution must read existing artifacts only"
                )
            if self.delegates_to_task_kind is not None:
                raise IncompatibleStateError(
                    "pure_analysis execution must not delegate to a task kind"
                )
            if self.delegation_count != 0:
                raise IncompatibleStateError("pure_analysis execution delegation_count must be 0")
            if self.side_effects is not SideEffects.NONE:
                raise IncompatibleStateError("pure_analysis execution side_effects must be 'none'")
            if self.writes_artifacts is not False:
                raise IncompatibleStateError("pure_analysis execution must not write artifacts")
        if self.network_access is not False:
            raise IncompatibleStateError("execution network_access must be false")

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "kind": self.kind.value,
            "starts_subprocess": self.starts_subprocess,
            "reads_existing_artifacts_only": self.reads_existing_artifacts_only,
            "delegates_to_task_kind": self.delegates_to_task_kind,
            "delegation_count": self.delegation_count,
            "side_effects": self.side_effects.value,
            "network_access": self.network_access,
            "writes_artifacts": self.writes_artifacts,
        }


@dataclass(frozen=True)
class ValidationLayer:
    """One declared validation layer owner and its coverage boundary."""

    owner: EvaluatedBy | str
    checks: tuple[str, ...] = ()
    does_not_cover: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "owner", _coerce_enum(EvaluatedBy, self.owner, field_name="layer.owner")
        )
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(self, "does_not_cover", tuple(self.does_not_cover))

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "owner": self.owner.value,
            "checks": list(self.checks),
            "does_not_cover": list(self.does_not_cover),
        }


@dataclass(frozen=True)
class ValidationLayers:
    """The three independent validation layers."""

    json_structure: ValidationLayer = field(
        default_factory=lambda: ValidationLayer(EvaluatedBy.CONTRACT)
    )
    cccp_scientific_semantics: ValidationLayer = field(
        default_factory=lambda: ValidationLayer(EvaluatedBy.CCCP)
    )
    runtime_platform_constraints: ValidationLayer = field(
        default_factory=lambda: ValidationLayer(EvaluatedBy.RUNTIME)
    )

    def __post_init__(self) -> None:
        expected = (
            ("json_structure", EvaluatedBy.CONTRACT),
            ("cccp_scientific_semantics", EvaluatedBy.CCCP),
            ("runtime_platform_constraints", EvaluatedBy.RUNTIME),
        )
        for field_name, owner in expected:
            layer = getattr(self, field_name)
            if layer.owner is not owner:
                raise IncompatibleStateError(
                    f"validation_layers.{field_name}.owner must be {owner.value!r}"
                )

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "json_structure": self.json_structure.to_dict(),
            "cccp_scientific_semantics": self.cccp_scientific_semantics.to_dict(),
            "runtime_platform_constraints": self.runtime_platform_constraints.to_dict(),
        }


@dataclass(frozen=True)
class UnknownFieldPolicy:
    """Frozen unknown-field policy for execution versus historical reads."""

    new_execution_request: str = "reject_unsupported_execution_fields"
    historical_read: str = UNKNOWN_FIELD_HISTORICAL_READ_POLICY
    unknown_major_version: str = "never_execute"
    extension_namespace: str = "x-acp2-<owner>-<name>"

    def __post_init__(self) -> None:
        expected = {
            "new_execution_request": "reject_unsupported_execution_fields",
            "historical_read": UNKNOWN_FIELD_HISTORICAL_READ_POLICY,
            "unknown_major_version": "never_execute",
            "extension_namespace": "x-acp2-<owner>-<name>",
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise IncompatibleStateError(
                    f"unknown_field_policy.{field_name} is frozen at {expected_value!r}"
                )

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "new_execution_request": self.new_execution_request,
            "historical_read": self.historical_read,
            "unknown_major_version": self.unknown_major_version,
            "extension_namespace": self.extension_namespace,
        }


@dataclass(frozen=True)
class Projection:
    """One independently reported capability state."""

    state: CapabilityState | str
    reason: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "state", _coerce_enum(CapabilityState, self.state, field_name="projection.state")
        )
        for field_name in ("reason", "evidence_ref"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CapabilityContractError(f"projection.{field_name} must be null or non-empty")

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        payload: dict[str, object] = {"state": self.state.value}
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.evidence_ref is not None:
            payload["evidence_ref"] = self.evidence_ref
        return payload


@dataclass(frozen=True)
class CapabilityProjections:
    """The four independent projections required by the frozen schema."""

    described: Projection = field(default_factory=lambda: Projection(CapabilityState.PRESENT))
    installed: Projection = field(default_factory=lambda: Projection(CapabilityState.UNKNOWN))
    available: Projection = field(default_factory=lambda: Projection(CapabilityState.UNKNOWN))
    verified: Projection = field(default_factory=lambda: Projection(CapabilityState.NOT_VERIFIED))

    def __post_init__(self) -> None:
        for name in ("described", "installed", "available", "verified"):
            projection = getattr(self, name)
            if not isinstance(projection, Projection):
                raise CapabilityContractError(f"projections.{name} must be a Projection")
            allowed = CAPABILITY_STATE_RULES[name]
            if projection.state not in allowed:
                allowed_text = ", ".join(sorted(state.value for state in allowed))
                raise IncompatibleStateError(
                    f"projections.{name}.state {projection.state.value!r} is "
                    f"incompatible; allowed: {allowed_text}"
                )
        if self.verified.state is CapabilityState.VERIFIED and not self.verified.evidence_ref:
            raise IncompatibleStateError(
                "a verified projection requires an evidence_ref; installation or "
                "availability never implies scientific verification"
            )

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object."""
        return {
            "described": self.described.to_dict(),
            "installed": self.installed.to_dict(),
            "available": self.available.to_dict(),
            "verified": self.verified.to_dict(),
        }


@dataclass(frozen=True)
class CapabilityDescriptor:
    """One frozen scientific capability descriptor.

    The descriptor separates what science is offered (``capability_id``)
    from the implementation that carries it (``implementation_id``).  It
    carries no platform job, account, queue or price field.
    """

    capability_id: str
    implementation_id: str
    input_schema: SchemaReference
    output_schema: SchemaReference
    execution_semantics: ExecutionSemantics
    preconditions: tuple[Precondition, ...] = ()
    completion_checks: tuple[CompletionCheck, ...] = ()
    limitations: tuple[Limitation, ...] = ()
    compatibility: Compatibility = field(default_factory=Compatibility)
    provenance_requirements: ProvenanceRequirements = field(default_factory=ProvenanceRequirements)
    validation_layers: ValidationLayers = field(default_factory=ValidationLayers)
    projections: CapabilityProjections = field(default_factory=CapabilityProjections)
    unknown_field_policy: UnknownFieldPolicy = field(default_factory=UnknownFieldPolicy)
    schema_version: int = CAPABILITY_SCHEMA_VERSION
    schema: str = CAPABILITY_SCHEMA

    def __post_init__(self) -> None:
        _require_capability_identifier(self.capability_id, field_name="capability_id")
        _require_capability_identifier(self.implementation_id, field_name="implementation_id")
        if self.schema != CAPABILITY_SCHEMA:
            raise IncompatibleStateError(
                f"schema must be {CAPABILITY_SCHEMA!r} (got {self.schema!r})"
            )
        if self.schema_version != CAPABILITY_SCHEMA_VERSION:
            raise UnknownMajorVersionError(
                f"unsupported capability schema version {self.schema_version!r}; "
                f"supported: {CAPABILITY_SCHEMA_VERSION}"
            )
        if not isinstance(self.input_schema, SchemaReference) or not isinstance(
            self.output_schema, SchemaReference
        ):
            raise CapabilityContractError("input_schema/output_schema must be SchemaReference")
        if not isinstance(self.execution_semantics, ExecutionSemantics):
            raise CapabilityContractError("execution_semantics must be ExecutionSemantics")
        for field_name in (
            "preconditions",
            "completion_checks",
            "limitations",
        ):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))
        for field_name in ("preconditions", "completion_checks", "limitations"):
            entries = getattr(self, field_name)
            ids = [entry.id for entry in entries]
            if len(ids) != len(set(ids)):
                raise CapabilityContractError(
                    f"{field_name} ids must be unique within a descriptor"
                )
        if not isinstance(self.compatibility, Compatibility):
            raise CapabilityContractError("compatibility must be Compatibility")
        if not isinstance(self.provenance_requirements, ProvenanceRequirements):
            raise CapabilityContractError("provenance_requirements must be ProvenanceRequirements")
        if not isinstance(self.projections, CapabilityProjections):
            raise CapabilityContractError("projections must be CapabilityProjections")
        if not isinstance(self.validation_layers, ValidationLayers):
            raise CapabilityContractError("validation_layers must be ValidationLayers")
        if not isinstance(self.unknown_field_policy, UnknownFieldPolicy):
            raise CapabilityContractError("unknown_field_policy must be UnknownFieldPolicy")

    @property
    def kind(self) -> CapabilityKind:
        """Return the execution semantics kind."""
        return self.execution_semantics.kind

    @property
    def delegates_to_task_kind(self) -> str | None:
        """Return the single delegated task kind, or ``None`` for analysis."""
        return self.execution_semantics.delegates_to_task_kind

    def to_dict(self) -> dict[str, object]:
        """Return the schema-shaped JSON object with a stable key order."""
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "implementation_id": self.implementation_id,
            "input_schema": self.input_schema.to_dict(),
            "output_schema": self.output_schema.to_dict(),
            "preconditions": [item.to_dict() for item in self.preconditions],
            "completion_checks": [item.to_dict() for item in self.completion_checks],
            "limitations": [item.to_dict() for item in self.limitations],
            "compatibility": self.compatibility.to_dict(),
            "provenance_requirements": self.provenance_requirements.to_dict(),
            "execution_semantics": self.execution_semantics.to_dict(),
            "validation_layers": self.validation_layers.to_dict(),
            "projections": self.projections.to_dict(),
            "unknown_field_policy": self.unknown_field_policy.to_dict(),
        }
