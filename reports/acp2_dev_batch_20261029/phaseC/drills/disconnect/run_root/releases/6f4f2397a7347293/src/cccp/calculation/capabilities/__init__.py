"""Scientific capability contracts and read-only registry (ACP2 science A-05).

This initializer exports only.  Implementation lives in the sibling modules:

* :mod:`~cccp.calculation.capabilities.contracts` — frozen descriptor types
  mirroring ``capability.schema.json`` and the typed contract errors;
* :mod:`~cccp.calculation.capabilities.registry` — the read-only registry,
  deterministic catalog and the four independent projections;
* :mod:`~cccp.calculation.capabilities.builtin` — the four seeded built-in
  capabilities, the unsupported-family declaration and the lazy pure-analysis
  entry point.

Importing this package pulls in no task-execution module, no QC interface and
never starts a calculation.
"""

from __future__ import annotations

from cccp.calculation.capabilities.builtin import (
    BUILTIN_CAPABILITY_IDS as BUILTIN_CAPABILITY_IDS,
)
from cccp.calculation.capabilities.builtin import (
    BUILTIN_DESCRIPTORS as BUILTIN_DESCRIPTORS,
)
from cccp.calculation.capabilities.builtin import (
    BUILTIN_FAMILIES as BUILTIN_FAMILIES,
)
from cccp.calculation.capabilities.builtin import (
    UNSUPPORTED_FAMILIES as UNSUPPORTED_FAMILIES,
)
from cccp.calculation.capabilities.builtin import (
    CapabilityFamilyDescription as CapabilityFamilyDescription,
)
from cccp.calculation.capabilities.builtin import (
    builtin_catalog as builtin_catalog,
)
from cccp.calculation.capabilities.builtin import (
    builtin_catalog_json as builtin_catalog_json,
)
from cccp.calculation.capabilities.builtin import (
    builtin_registry as builtin_registry,
)
from cccp.calculation.capabilities.builtin import (
    describe_family as describe_family,
)
from cccp.calculation.capabilities.builtin import (
    resolve_analysis_entrypoint as resolve_analysis_entrypoint,
)
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
from cccp.calculation.capabilities.domains import (
    DOMAIN_CAPABILITY_IDS as DOMAIN_CAPABILITY_IDS,
)
from cccp.calculation.capabilities.domains import (
    DOMAIN_DESCRIPTORS as DOMAIN_DESCRIPTORS,
)
from cccp.calculation.capabilities.domains import (
    DOMAIN_EXTENSION_VERSION as DOMAIN_EXTENSION_VERSION,
)
from cccp.calculation.capabilities.domains import (
    DOMAIN_FAMILIES as DOMAIN_FAMILIES,
)
from cccp.calculation.capabilities.domains import (
    describe_domain_family as describe_domain_family,
)
from cccp.calculation.capabilities.domains import (
    domain_catalog as domain_catalog,
)
from cccp.calculation.capabilities.domains import (
    domain_catalog_json as domain_catalog_json,
)
from cccp.calculation.capabilities.domains import (
    domain_registry as domain_registry,
)
from cccp.calculation.capabilities.domains import (
    require_domain_descriptor as require_domain_descriptor,
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
from cccp.calculation.capabilities.task_binding import (
    BINDABLE_TASK_KINDS as BINDABLE_TASK_KINDS,
)
from cccp.calculation.capabilities.task_binding import (
    EXISTING_TASK_KINDS as EXISTING_TASK_KINDS,
)
from cccp.calculation.capabilities.task_binding import (
    REQUEST_CONTRACT_REF as REQUEST_CONTRACT_REF,
)
from cccp.calculation.capabilities.task_binding import (
    RESULT_CONTRACT_REF as RESULT_CONTRACT_REF,
)
from cccp.calculation.capabilities.task_binding import (
    SUPPORTED_COMPUTE_TASK_KINDS as SUPPORTED_COMPUTE_TASK_KINDS,
)
from cccp.calculation.capabilities.task_binding import (
    TASK_BINDING_CONTRACT_VERSION as TASK_BINDING_CONTRACT_VERSION,
)
from cccp.calculation.capabilities.task_binding import (
    TASK_BINDING_SCHEMA as TASK_BINDING_SCHEMA,
)
from cccp.calculation.capabilities.task_binding import (
    TASK_BINDING_SCHEMA_VERSION as TASK_BINDING_SCHEMA_VERSION,
)
from cccp.calculation.capabilities.task_binding import (
    TASK_ENTRYPOINTS as TASK_ENTRYPOINTS,
)
from cccp.calculation.capabilities.task_binding import (
    BindingSelection as BindingSelection,
)
from cccp.calculation.capabilities.task_binding import (
    BindingSelectionMode as BindingSelectionMode,
)
from cccp.calculation.capabilities.task_binding import (
    PreflightCheck as PreflightCheck,
)
from cccp.calculation.capabilities.task_binding import (
    PreflightOutcome as PreflightOutcome,
)
from cccp.calculation.capabilities.task_binding import (
    PreflightResult as PreflightResult,
)
from cccp.calculation.capabilities.task_binding import (
    ScientificPreconditionError as ScientificPreconditionError,
)
from cccp.calculation.capabilities.task_binding import (
    TaskBinding as TaskBinding,
)
from cccp.calculation.capabilities.task_binding import (
    TaskBindingError as TaskBindingError,
)
from cccp.calculation.capabilities.task_binding import (
    TaskEntrypoint as TaskEntrypoint,
)
from cccp.calculation.capabilities.task_binding import (
    TaskKindReference as TaskKindReference,
)
from cccp.calculation.capabilities.task_binding import (
    UnknownTaskKindError as UnknownTaskKindError,
)
from cccp.calculation.capabilities.task_binding import (
    UnsupportedBindingError as UnsupportedBindingError,
)
from cccp.calculation.capabilities.task_binding import (
    bind_capability as bind_capability,
)
from cccp.calculation.capabilities.task_binding import (
    bind_task_kind as bind_task_kind,
)
from cccp.calculation.capabilities.task_binding import (
    build_task_request as build_task_request,
)
from cccp.calculation.capabilities.task_binding import (
    execute_binding as execute_binding,
)
from cccp.calculation.capabilities.task_binding import (
    preflight_binding as preflight_binding,
)

__all__ = [
    "BACKWARD_COMPATIBLE_ADDITIONS",
    "BUILTIN_CAPABILITY_IDS",
    "BUILTIN_DESCRIPTORS",
    "BUILTIN_FAMILIES",
    "CAPABILITY_CATALOG_SCHEMA",
    "CAPABILITY_CONTRACT_VERSION",
    "CAPABILITY_SCHEMA",
    "CAPABILITY_SCHEMA_VERSION",
    "CAPABILITY_STATE_RULES",
    "COMPLETION_FAILURE_ERROR_CODES",
    "DOMAIN_CAPABILITY_IDS",
    "DOMAIN_DESCRIPTORS",
    "DOMAIN_EXTENSION_VERSION",
    "DOMAIN_FAMILIES",
    "CONTRACT_VERSION",
    "ERROR_CODES",
    "HISTORICAL_UNKNOWN_FIELDS_POLICY",
    "SUPPORTED_MAJOR_VERSIONS",
    "TASK_KINDS",
    "UNKNOWN_FIELD_HISTORICAL_READ_POLICY",
    "UNKNOWN_MAJOR_VERSION_POLICY",
    "UNSUPPORTED_FAMILIES",
    "CapabilityCatalog",
    "CapabilityContractError",
    "CapabilityDescriptor",
    "CapabilityErrorRecord",
    "CapabilityFamilyDescription",
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
    "builtin_catalog",
    "builtin_catalog_json",
    "builtin_registry",
    "describe_domain_family",
    "domain_catalog",
    "domain_catalog_json",
    "domain_registry",
    "require_domain_descriptor",
    "describe_family",
    "resolve_analysis_entrypoint",
    "BINDABLE_TASK_KINDS",
    "EXISTING_TASK_KINDS",
    "REQUEST_CONTRACT_REF",
    "RESULT_CONTRACT_REF",
    "SUPPORTED_COMPUTE_TASK_KINDS",
    "TASK_BINDING_CONTRACT_VERSION",
    "TASK_BINDING_SCHEMA",
    "TASK_BINDING_SCHEMA_VERSION",
    "TASK_ENTRYPOINTS",
    "BindingSelection",
    "BindingSelectionMode",
    "PreflightCheck",
    "PreflightOutcome",
    "PreflightResult",
    "ScientificPreconditionError",
    "TaskBinding",
    "TaskBindingError",
    "TaskEntrypoint",
    "TaskKindReference",
    "UnknownTaskKindError",
    "UnsupportedBindingError",
    "bind_capability",
    "bind_task_kind",
    "build_task_request",
    "execute_binding",
    "preflight_binding",
]
