"""Scientific capability contracts and read-only registry (ACP2 science A-05).

This initializer exports only.  Implementation lives in the sibling modules:

* :mod:`~cccp.calculation.capabilities.contracts` — frozen descriptor types
  mirroring ``capability.schema.json`` and the typed contract errors;
* :mod:`~cccp.calculation.capabilities.registry` — the read-only registry,
  deterministic catalog and the four independent projections.

Importing this package pulls in no task-execution module, no QC interface and
never starts a calculation.  The built-in capability catalog is deliberately
not seeded here; A-05b owns that.
"""

from __future__ import annotations

from cccp.calculation.capabilities.contracts import (
    BACKWARD_COMPATIBLE_ADDITIONS as BACKWARD_COMPATIBLE_ADDITIONS,
)
from cccp.calculation.capabilities.contracts import (
    CAPABILITY_CONTRACT_VERSION as CAPABILITY_CONTRACT_VERSION,
)
from cccp.calculation.capabilities.contracts import (
    CAPABILITY_SCHEMA as CAPABILITY_SCHEMA,
)
from cccp.calculation.capabilities.contracts import (
    CAPABILITY_SCHEMA_VERSION as CAPABILITY_SCHEMA_VERSION,
)
from cccp.calculation.capabilities.contracts import (
    CAPABILITY_STATE_RULES as CAPABILITY_STATE_RULES,
)
from cccp.calculation.capabilities.contracts import (
    COMPLETION_FAILURE_ERROR_CODES as COMPLETION_FAILURE_ERROR_CODES,
)
from cccp.calculation.capabilities.contracts import (
    CONTRACT_VERSION as CONTRACT_VERSION,
)
from cccp.calculation.capabilities.contracts import (
    ERROR_CODES as ERROR_CODES,
)
from cccp.calculation.capabilities.contracts import (
    HISTORICAL_UNKNOWN_FIELDS_POLICY as HISTORICAL_UNKNOWN_FIELDS_POLICY,
)
from cccp.calculation.capabilities.contracts import (
    SUPPORTED_MAJOR_VERSIONS as SUPPORTED_MAJOR_VERSIONS,
)
from cccp.calculation.capabilities.contracts import (
    TASK_KINDS as TASK_KINDS,
)
from cccp.calculation.capabilities.contracts import (
    UNKNOWN_FIELD_HISTORICAL_READ_POLICY as UNKNOWN_FIELD_HISTORICAL_READ_POLICY,
)
from cccp.calculation.capabilities.contracts import (
    UNKNOWN_MAJOR_VERSION_POLICY as UNKNOWN_MAJOR_VERSION_POLICY,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityContractError as CapabilityContractError,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityDescriptor as CapabilityDescriptor,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityKind as CapabilityKind,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityProjections as CapabilityProjections,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityState as CapabilityState,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityStateError as CapabilityStateError,
)
from cccp.calculation.capabilities.contracts import (
    Compatibility as Compatibility,
)
from cccp.calculation.capabilities.contracts import (
    CompletionCheck as CompletionCheck,
)
from cccp.calculation.capabilities.contracts import (
    DuplicateCapabilityError as DuplicateCapabilityError,
)
from cccp.calculation.capabilities.contracts import (
    DuplicateImplementationError as DuplicateImplementationError,
)
from cccp.calculation.capabilities.contracts import (
    EvaluatedBy as EvaluatedBy,
)
from cccp.calculation.capabilities.contracts import (
    ExecutionSemantics as ExecutionSemantics,
)
from cccp.calculation.capabilities.contracts import (
    IncompatibleStateError as IncompatibleStateError,
)
from cccp.calculation.capabilities.contracts import (
    Limitation as Limitation,
)
from cccp.calculation.capabilities.contracts import (
    LimitationSeverity as LimitationSeverity,
)
from cccp.calculation.capabilities.contracts import (
    Precondition as Precondition,
)
from cccp.calculation.capabilities.contracts import (
    PreconditionKind as PreconditionKind,
)
from cccp.calculation.capabilities.contracts import (
    Projection as Projection,
)
from cccp.calculation.capabilities.contracts import (
    ProvenanceRequirements as ProvenanceRequirements,
)
from cccp.calculation.capabilities.contracts import (
    SchemaReference as SchemaReference,
)
from cccp.calculation.capabilities.contracts import (
    SideEffects as SideEffects,
)
from cccp.calculation.capabilities.contracts import (
    UnknownCapabilityError as UnknownCapabilityError,
)
from cccp.calculation.capabilities.contracts import (
    UnknownFieldPolicy as UnknownFieldPolicy,
)
from cccp.calculation.capabilities.contracts import (
    UnknownImplementationError as UnknownImplementationError,
)
from cccp.calculation.capabilities.contracts import (
    UnknownMajorVersionError as UnknownMajorVersionError,
)
from cccp.calculation.capabilities.contracts import (
    ValidationLayer as ValidationLayer,
)
from cccp.calculation.capabilities.contracts import (
    ValidationLayers as ValidationLayers,
)
from cccp.calculation.capabilities.registry import (
    CAPABILITY_CATALOG_SCHEMA as CAPABILITY_CATALOG_SCHEMA,
)
from cccp.calculation.capabilities.registry import (
    CapabilityCatalog as CapabilityCatalog,
)
from cccp.calculation.capabilities.registry import (
    CapabilityErrorRecord as CapabilityErrorRecord,
)
from cccp.calculation.capabilities.registry import (
    CapabilityQueryResult as CapabilityQueryResult,
)
from cccp.calculation.capabilities.registry import (
    CapabilityRecord as CapabilityRecord,
)
from cccp.calculation.capabilities.registry import (
    CapabilityRegistry as CapabilityRegistry,
)
from cccp.calculation.capabilities.registry import (
    ExecutableResolver as ExecutableResolver,
)

__all__ = [
    "BACKWARD_COMPATIBLE_ADDITIONS",
    "CAPABILITY_CATALOG_SCHEMA",
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
    "CapabilityCatalog",
    "CapabilityContractError",
    "CapabilityDescriptor",
    "CapabilityErrorRecord",
    "CapabilityKind",
    "CapabilityProjections",
    "CapabilityQueryResult",
    "CapabilityRecord",
    "CapabilityRegistry",
    "CapabilityState",
    "CapabilityStateError",
    "Compatibility",
    "CompletionCheck",
    "DuplicateCapabilityError",
    "DuplicateImplementationError",
    "EvaluatedBy",
    "ExecutionSemantics",
    "ExecutableResolver",
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
    "UnknownFieldPolicy",
    "UnknownImplementationError",
    "UnknownMajorVersionError",
    "ValidationLayer",
    "ValidationLayers",
]
