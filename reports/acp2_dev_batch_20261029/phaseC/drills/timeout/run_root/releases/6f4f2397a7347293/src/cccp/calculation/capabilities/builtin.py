"""Built-in scientific capability catalog (ACP2 science A-05b).

This module seeds the read-only registry with exactly four scientific
capabilities and nothing else:

* ``electronic.energy``  -> existing task kind ``singlepoint`` (compute);
* ``geometry.optimize``  -> existing task kind ``optimize`` (compute);
* ``vibration.compute``  -> existing task kind ``frequency`` (compute);
* ``vibration.analyze``  -> the existing analysis layer (pure analysis).

The three compute descriptors bind only by *task-kind reference*; this module
never imports :mod:`cccp.calculation.tasks`, never resolves a backend and
never starts a subprocess at discovery time.  The pure-analysis descriptor
declares ``starts_subprocess=False`` and resolves lazily to
:func:`cccp.analysis.frequency_products.build_frequency_report`, which reads
existing frequency/Hessian products.  It must never route to
``run_frequency``: computing a frequency and analysing an existing one are
different scientific acts.

Capability families that are not seeded here are reported explicitly as
``described=False`` through :func:`describe_family`; no descriptor is invented
for them.  Registration is deterministic: descriptors are sorted by
capability id and the registry preserves that order.

Author: QCcalc Team
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from cccp.calculation.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityKind,
    CapabilityProjections,
    CapabilityState,
    CapabilityStateError,
    CompletionCheck,
    ExecutionSemantics,
    Limitation,
    Precondition,
    Projection,
    ProvenanceRequirements,
    SchemaReference,
    UnknownCapabilityError,
)
from cccp.calculation.capabilities.registry import (
    CapabilityCatalog,
    CapabilityRegistry,
    ExecutableResolver,
)

__all__ = [
    "BUILTIN_CAPABILITY_IDS",
    "BUILTIN_DESCRIPTORS",
    "BUILTIN_FAMILIES",
    "UNSUPPORTED_FAMILIES",
    "CapabilityFamilyDescription",
    "builtin_catalog",
    "builtin_catalog_json",
    "builtin_registry",
    "describe_family",
    "resolve_analysis_entrypoint",
]

#: Input/output references reused from the existing CCCP task contracts.
_COMPUTE_INPUT_SCHEMA = SchemaReference("cccp.calculation.requests#TaskRequest")
_COMPUTE_OUTPUT_SCHEMA = SchemaReference("cccp.calculation.results#TaskResult")
_ANALYSIS_INPUT_SCHEMA = SchemaReference("cccp.analysis.orca_parser#OrcaCalculation")
_ANALYSIS_OUTPUT_SCHEMA = SchemaReference("cccp.analysis.frequency_products#build_frequency_report")

#: Scientific preconditions shared by the three compute capabilities.
_STRUCTURE_PRECONDITION = Precondition(
    id="structure_supplied",
    kind="input",
    description="A resolvable molecular structure is supplied.",
    required=True,
    error_code="invalid_scientific_input",
    evaluated_by="cccp",
)
_ELECTRONIC_STATE_PRECONDITION = Precondition(
    id="charge_multiplicity_consistent",
    kind="scientific",
    description="Charge and multiplicity are consistent with the electron count.",
    required=True,
    error_code="invalid_scientific_input",
    evaluated_by="cccp",
)
_QC_EXECUTABLE_PRECONDITION = Precondition(
    id="qc_executable_available",
    kind="environment",
    description=(
        "A real QC executable is resolvable by the existing software seam at execution time."
    ),
    required=True,
    error_code="unavailable_binary",
    evaluated_by="runtime",
)
_COMPUTE_PRECONDITIONS = (
    _STRUCTURE_PRECONDITION,
    _ELECTRONIC_STATE_PRECONDITION,
    _QC_EXECUTABLE_PRECONDITION,
)

#: The pure-analysis precondition: existing products, never a new calculation.
_FREQUENCY_PRODUCTS_PRECONDITION = Precondition(
    id="frequency_products_available",
    kind="artifact",
    description=(
        "Existing frequency or Hessian products are supplied; the analysis "
        "layer never recomputes them."
    ),
    required=True,
    error_code="invalid_scientific_input",
    evaluated_by="cccp",
)


def _compute_execution(task_kind: str) -> ExecutionSemantics:
    """Return compute semantics that delegate to one existing task kind."""
    return ExecutionSemantics(
        kind=CapabilityKind.COMPUTE,
        starts_subprocess=True,
        reads_existing_artifacts_only=False,
        delegates_to_task_kind=task_kind,
        delegation_count=1,
        side_effects="artifacts_only",
        writes_artifacts=True,
    )


_ANALYSIS_EXECUTION = ExecutionSemantics(
    kind=CapabilityKind.PURE_ANALYSIS,
    starts_subprocess=False,
    reads_existing_artifacts_only=True,
    delegates_to_task_kind=None,
    delegation_count=0,
    side_effects="none",
    writes_artifacts=False,
)

_ANALYSIS_PROJECTIONS = CapabilityProjections(
    described=Projection(
        CapabilityState.PRESENT,
        reason="registered in the read-only built-in capability catalog",
    ),
    installed=Projection(
        CapabilityState.NOT_VERIFIED,
        reason="pure analysis reads existing artifacts; no executable is required",
    ),
    available=Projection(
        CapabilityState.PRESENT,
        reason="pure analysis requires no executable",
    ),
    verified=Projection(CapabilityState.NOT_VERIFIED),
)

_ANALYSIS_PROVENANCE = ProvenanceRequirements(
    method_identity="not_applicable",
    notes=(
        "Method identity is inherited from the source frequency products; "
        "analysis does not choose a level of theory.",
    ),
)

_ELECTRONIC_ENERGY = CapabilityDescriptor(
    capability_id="electronic.energy",
    implementation_id="cccp.calculation.tasks.singlepoint",
    input_schema=_COMPUTE_INPUT_SCHEMA,
    output_schema=_COMPUTE_OUTPUT_SCHEMA,
    execution_semantics=_compute_execution("singlepoint"),
    preconditions=_COMPUTE_PRECONDITIONS,
    completion_checks=(
        CompletionCheck(
            id="scf_converged",
            description=("The single-point SCF converged and reported a finite final energy."),
            evaluator="cccp.calculation.tasks.singlepoint",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("scf_convergence", "final_energy"),
        ),
    ),
    limitations=(
        Limitation(
            id="no_geometry_relaxation",
            text=(
                "This capability evaluates one supplied structure; it does not "
                "optimize the geometry."
            ),
            severity="info",
            applies_to="energies from a single supplied geometry",
        ),
    ),
)

_GEOMETRY_OPTIMIZE = CapabilityDescriptor(
    capability_id="geometry.optimize",
    implementation_id="cccp.calculation.tasks.optimize",
    input_schema=_COMPUTE_INPUT_SCHEMA,
    output_schema=_COMPUTE_OUTPUT_SCHEMA,
    execution_semantics=_compute_execution("optimize"),
    preconditions=_COMPUTE_PRECONDITIONS,
    completion_checks=(
        CompletionCheck(
            id="optimization_converged",
            description="The geometry optimization reached its convergence criteria.",
            evaluator="cccp.calculation.tasks.optimize",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("convergence", "final_geometry"),
        ),
    ),
    limitations=(
        Limitation(
            id="convergence_is_not_a_minimum",
            text=(
                "A converged geometry is not proof of a minimum; a separate "
                "vibration.compute result is required to classify the stationary point."
            ),
            severity="warning",
            applies_to="stationary-point classification",
        ),
    ),
)

_VIBRATION_COMPUTE = CapabilityDescriptor(
    capability_id="vibration.compute",
    implementation_id="cccp.calculation.tasks.frequency",
    input_schema=_COMPUTE_INPUT_SCHEMA,
    output_schema=_COMPUTE_OUTPUT_SCHEMA,
    execution_semantics=_compute_execution("frequency"),
    preconditions=_COMPUTE_PRECONDITIONS,
    completion_checks=(
        CompletionCheck(
            id="frequency_modes_available",
            description=("The frequency task completed and produced a finite frequency set."),
            evaluator="cccp.calculation.tasks.frequency",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("frequencies", "imaginary_frequencies"),
        ),
    ),
    limitations=(
        Limitation(
            id="imaginary_modes_are_not_a_path",
            text=(
                "An imaginary mode is a local curvature fact; it does not by itself "
                "establish a reaction path or connectivity."
            ),
            severity="warning",
            applies_to="imaginary frequencies",
        ),
    ),
)

_VIBRATION_ANALYZE = CapabilityDescriptor(
    capability_id="vibration.analyze",
    implementation_id="cccp.analysis.frequency_products",
    input_schema=_ANALYSIS_INPUT_SCHEMA,
    output_schema=_ANALYSIS_OUTPUT_SCHEMA,
    execution_semantics=_ANALYSIS_EXECUTION,
    preconditions=(_FREQUENCY_PRODUCTS_PRECONDITION,),
    completion_checks=(
        CompletionCheck(
            id="frequency_products_readable",
            description=(
                "Existing frequency products were projected into the normal-modes "
                "report without recomputation."
            ),
            evaluator="cccp.analysis.frequency_products",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("frequencies", "normal_modes"),
        ),
    ),
    limitations=(
        Limitation(
            id="reads_existing_products_only",
            text=(
                "This capability never launches a QC program; missing frequencies or "
                "vectors are reported as warnings, not recomputed."
            ),
            severity="hard_limit",
            applies_to="all inputs",
        ),
    ),
    provenance_requirements=_ANALYSIS_PROVENANCE,
    projections=_ANALYSIS_PROJECTIONS,
)

#: The catalog, sorted by capability id (the registry's deterministic order).
BUILTIN_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = tuple(
    sorted(
        (
            _ELECTRONIC_ENERGY,
            _GEOMETRY_OPTIMIZE,
            _VIBRATION_ANALYZE,
            _VIBRATION_COMPUTE,
        ),
        key=lambda descriptor: descriptor.capability_id,
    )
)

#: The four seeded capability ids, in deterministic order.
BUILTIN_CAPABILITY_IDS: tuple[str, ...] = tuple(
    descriptor.capability_id for descriptor in BUILTIN_DESCRIPTORS
)

_DESCRIPTOR_BY_ID: Mapping[str, CapabilityDescriptor] = MappingProxyType(
    {descriptor.capability_id: descriptor for descriptor in BUILTIN_DESCRIPTORS}
)

#: Families with at least one seeded capability.
BUILTIN_FAMILIES: tuple[str, ...] = tuple(
    sorted({capability_id.split(".", 1)[0] for capability_id in BUILTIN_CAPABILITY_IDS})
)

#: Capability families deliberately left unsupported by the built-in catalog.
UNSUPPORTED_FAMILIES: tuple[str, ...] = (
    "casscf",
    "clustering",
    "censo_refine",
    "conformer_search",
    "constrained_optimization",
    "gradient",
    "md_sampling",
    "nmr",
    "reaction_path",
    "thermochemistry",
)

#: The only lazy analysis entry point known to the built-in catalog.
_ANALYSIS_ENTRYPOINTS: Mapping[str, str] = MappingProxyType(
    {"vibration.analyze": "build_frequency_report"}
)


@dataclass(frozen=True)
class CapabilityFamilyDescription:
    """Explicit support statement for one capability family.

    It is a support view, not a capability descriptor: an unsupported family
    is reported with ``described=False`` and carries no invented fields.
    """

    family: str
    described: bool
    supported: bool
    capability_ids: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe family description."""
        return {
            "family": self.family,
            "described": self.described,
            "supported": self.supported,
            "capability_ids": list(self.capability_ids),
            "reason": self.reason,
        }


def builtin_registry(
    resolver: ExecutableResolver | None = None,
) -> CapabilityRegistry:
    """Return a registry seeded with exactly the built-in descriptors.

    Args:
        resolver: Optional executable probe used for the ``installed`` and
            ``available`` projections.  When omitted the registry uses the
            production :func:`cccp.software.resolve_executable` seam lazily.
    """
    return CapabilityRegistry(BUILTIN_DESCRIPTORS, resolver=resolver)


def builtin_catalog(resolver: ExecutableResolver | None = None) -> CapabilityCatalog:
    """Return the deterministically ordered built-in catalog."""
    return builtin_registry(resolver=resolver).catalog()


def builtin_catalog_json(resolver: ExecutableResolver | None = None) -> str:
    """Return the canonical serialized built-in catalog."""
    return builtin_registry(resolver=resolver).catalog_json()


def describe_family(family: str) -> CapabilityFamilyDescription:
    """Describe whether a capability family is supported.

    Supported families list their seeded capabilities.  Every other family
    (explicitly unsupported or simply unknown) returns ``described=False``;
    no descriptor is fabricated for it.
    """
    normalized = family.strip().lower() if isinstance(family, str) else ""
    capability_ids = tuple(
        capability_id
        for capability_id in BUILTIN_CAPABILITY_IDS
        if capability_id.split(".", 1)[0] == normalized
    )
    if capability_ids:
        return CapabilityFamilyDescription(
            family=normalized,
            described=True,
            supported=True,
            capability_ids=capability_ids,
            reason="family has seeded capabilities in the built-in catalog",
        )
    reason = (
        "family is explicitly unsupported by the built-in catalog; no descriptor is declared"
        if normalized in UNSUPPORTED_FAMILIES
        else "family is not declared by the built-in catalog; no descriptor is declared"
    )
    return CapabilityFamilyDescription(
        family=normalized,
        described=False,
        supported=False,
        reason=reason,
    )


def resolve_analysis_entrypoint(capability_id: str) -> Callable[[Any], dict[str, Any]]:
    """Return the pure-analysis callable for *capability_id* (lazy import).

    Only pure-analysis capabilities are resolvable here.  Compute capabilities
    must go through the existing task binding/execution seam, so asking for one
    is a typed state error rather than a silent task dispatch.  The import is
    lazy: catalog discovery never imports the analysis module, and this path
    never imports :mod:`cccp.calculation.tasks.frequency`.
    """
    descriptor = _DESCRIPTOR_BY_ID.get(capability_id)
    if descriptor is None:
        raise UnknownCapabilityError(f"capability {capability_id!r} is not in the built-in catalog")
    if descriptor.kind is not CapabilityKind.PURE_ANALYSIS:
        raise CapabilityStateError(
            f"capability {capability_id!r} is a compute capability; analysis "
            "resolution is only defined for pure-analysis capabilities"
        )
    entry_name = _ANALYSIS_ENTRYPOINTS.get(capability_id)
    if entry_name is None:
        raise CapabilityStateError(
            f"capability {capability_id!r} has no declared analysis entry point"
        )
    module = importlib.import_module(descriptor.implementation_id)
    entry = getattr(module, entry_name, None)
    if not callable(entry):
        raise CapabilityStateError(
            f"analysis entry {descriptor.implementation_id}.{entry_name} is missing or not callable"
        )
    return entry
