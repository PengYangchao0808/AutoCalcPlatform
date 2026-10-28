"""Domain capability descriptors bound after the frozen built-in catalog.

``builtin.py`` and its ``handoff-H2.json`` snapshot are frozen at the A-05b
scope (four capabilities).  A-24 adds the NMR shielding/comparison capability
and A-24b adds ensemble thermochemistry in this explicit extension catalog, so
the built-in snapshot and its digest chain stay unchanged.

The descriptors follow the same frozen ``acp2.science.capability`` contract and
delegate to the same single implementation seam:

* ``nmr.shielding`` (compute) -> ``nmr_shielding`` task core;
* ``nmr.compare`` (pure analysis) -> ``cccp.analysis.nmr.comparison``;
* ``thermochemistry.ensemble`` (pure analysis) -> ``cccp.analysis.ensemble.science``.

``domain_registry()`` returns the built-in descriptors plus these extensions,
so callers opt in explicitly and the binding can resolve the analysis entry
point without a second registry implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from cccp.calculation.capabilities.builtin import (
    BUILTIN_DESCRIPTORS,
    CapabilityFamilyDescription,
)
from cccp.calculation.capabilities.contracts import (
    CapabilityDescriptor,
    CapabilityProjections,
    CapabilityState,
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
    "DOMAIN_CAPABILITY_IDS",
    "DOMAIN_DESCRIPTORS",
    "DOMAIN_EXTENSION_VERSION",
    "DOMAIN_FAMILIES",
    "describe_domain_family",
    "domain_catalog",
    "domain_catalog_json",
    "domain_registry",
]

DOMAIN_EXTENSION_VERSION = 1
DOMAIN_FAMILIES: tuple[str, ...] = ("nmr", "thermochemistry")

_COMPUTE_INPUT_SCHEMA = SchemaReference("cccp.calculation.requests#TaskRequest")
_COMPUTE_OUTPUT_SCHEMA = SchemaReference("cccp.calculation.results#TaskResult")

_ANALYSIS_EXECUTION = ExecutionSemantics(
    kind="pure_analysis",
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
        reason="registered in the A-24 domain extension catalog",
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

_COMPUTE_PRECONDITIONS = (
    Precondition(
        id="structure_supplied",
        kind="input",
        description="A resolvable molecular structure is supplied.",
        required=True,
        error_code="invalid_scientific_input",
        evaluated_by="cccp",
    ),
    Precondition(
        id="charge_multiplicity_consistent",
        kind="scientific",
        description="Charge and multiplicity are consistent with the electron count.",
        required=True,
        error_code="invalid_scientific_input",
        evaluated_by="cccp",
    ),
    Precondition(
        id="qc_executable_available",
        kind="environment",
        description=(
            "A real QC executable is resolvable by the existing software seam at execution time."
        ),
        required=True,
        error_code="unavailable_binary",
        evaluated_by="runtime",
    ),
)

_NMR_SHIELDING = CapabilityDescriptor(
    capability_id="nmr.shielding",
    implementation_id="cccp.calculation.tasks.nmr_shielding",
    input_schema=_COMPUTE_INPUT_SCHEMA,
    output_schema=_COMPUTE_OUTPUT_SCHEMA,
    execution_semantics=ExecutionSemantics(
        kind="compute",
        starts_subprocess=True,
        reads_existing_artifacts_only=False,
        delegates_to_task_kind="nmr_shielding",
        delegation_count=1,
        side_effects="artifacts_only",
        writes_artifacts=True,
    ),
    preconditions=_COMPUTE_PRECONDITIONS
    + (
        Precondition(
            id="atom_index_base_declared",
            kind="scientific",
            description=(
                "The atom-index base of the shielding table is declared so the table "
                "keys keep their identity between the task and the comparison."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
    ),
    completion_checks=(
        CompletionCheck(
            id="shielding_table_available",
            description=(
                "The task completed and produced a finite shielding table keyed in the "
                "declared atom-index base."
            ),
            evaluator="cccp.calculation.tasks.nmr_shielding",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("shieldings", "atom_index_base"),
        ),
    ),
    limitations=(
        Limitation(
            id="shielding_table_only",
            text=(
                "The task stops at the GIAO shielding table. TMS conversion, atom "
                "mapping, assignment, calibration and model-asset evidence belong to "
                "nmr.compare."
            ),
            severity="hard_limit",
            applies_to="all shielding results",
        ),
    ),
)

_NMR_COMPARE = CapabilityDescriptor(
    capability_id="nmr.compare",
    implementation_id="cccp.analysis.nmr.comparison",
    input_schema=SchemaReference("cccp.analysis.nmr.comparison#NmrShieldingComparisonInput"),
    output_schema=SchemaReference("cccp.analysis.nmr.comparison#NmrComparisonOutcome"),
    execution_semantics=_ANALYSIS_EXECUTION,
    preconditions=(
        Precondition(
            id="atom_mapping_declared",
            kind="scientific",
            description=(
                "A structure-order atom mapping (symbols, unique labels and index base) "
                "is declared for the shielding table."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="experimental_spectrum_recorded",
            kind="artifact",
            description=(
                "The experimental peak list is recorded as a digested source artifact; "
                "peaks are never invented or silently dropped."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="reference_conditions_declared",
            kind="scientific",
            description=(
                "The method/basis, solvent, TMS reference and scaling mode in force are "
                "declared for every required nucleus."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="model_assets_available",
            kind="artifact",
            description=(
                "The packaged DP5/FCHL model assets are resolvable and content-addressed; "
                "a missing asset is reported as unavailable, never ignored."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
    ),
    completion_checks=(
        CompletionCheck(
            id="atom_mapping_consistent",
            description=(
                "Every computed signal label follows the declared structure-order "
                "mapping and the ordered assignment key is recorded."
            ),
            evaluator="cccp.analysis.nmr.comparison",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("atom_mapping_digest", "atom_labels"),
        ),
        CompletionCheck(
            id="assignment_coverage_complete",
            description=(
                "Every experimental peak and every computed signal was paired exactly "
                "once; unmatched entries are reported, never dropped."
            ),
            evaluator="cccp.analysis.nmr.comparison",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("peak_count", "paired_peak_count"),
        ),
        CompletionCheck(
            id="reference_conditions_bound",
            description=(
                "Every required nucleus has a declared TMS reference and the "
                "solvent/method/basis/scaling conditions are recorded in provenance."
            ),
            evaluator="cccp.analysis.nmr.comparison",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("reference_shieldings", "solvent", "scaling_mode"),
        ),
        CompletionCheck(
            id="model_assets_present",
            description=(
                "The model-asset version is content-addressed and the DP5 assets are "
                "present before any model-based claim is made."
            ),
            evaluator="cccp.analysis.nmr.comparison",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("model_asset_version", "model_assets_available"),
        ),
    ),
    limitations=(
        Limitation(
            id="comparison_reads_existing_products",
            text=(
                "This capability never launches GIAO or conformer sampling; it compares "
                "an existing shielding table with a supplied experimental peak list."
            ),
            severity="hard_limit",
            applies_to="all inputs",
        ),
        Limitation(
            id="incomplete_reference_blocks_confidence",
            text=(
                "A missing required reference, partial coverage or a missing model asset "
                "downgrades the report to inconclusive/unavailable; it is never filled "
                "with a gas-phase or placeholder value."
            ),
            severity="warning",
            applies_to="reference and coverage checks",
        ),
        Limitation(
            id="no_experimental_quantitative_claim",
            text=(
                "The comparison reports assignment and calibration facts; it does not "
                "claim a quantitative experimental prediction."
            ),
            severity="info",
            applies_to="scientific interpretation",
        ),
    ),
    provenance_requirements=ProvenanceRequirements(
        method_identity="required",
        notes=(
            "Provenance records the molecule/atom mapping and its ordered assignment key, "
            "the digested experimental-spectrum source, the solvent/TMS reference/scaling "
            "conditions, the model-asset version and the model-asset availability.",
        ),
    ),
    projections=_ANALYSIS_PROJECTIONS,
)

_THERMOCHEMISTRY_ENSEMBLE = CapabilityDescriptor(
    capability_id="thermochemistry.ensemble",
    implementation_id="cccp.analysis.ensemble.science",
    input_schema=SchemaReference("cccp.analysis.ensemble.science#EnsembleThermochemistryInput"),
    output_schema=SchemaReference("cccp.analysis.ensemble.science#EnsembleThermochemistryOutcome"),
    execution_semantics=_ANALYSIS_EXECUTION,
    preconditions=(
        Precondition(
            id="components_declared",
            kind="input",
            description=("At least one ensemble component with a unique component_id is declared."),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="component_weights_declared",
            kind="scientific",
            description=(
                "Every component carries a finite positive weight; the supplied form "
                "is raw or explicitly normalized, never silently converted."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="temperature_declared",
            kind="scientific",
            description="A positive finite ensemble temperature is declared.",
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
        Precondition(
            id="standard_state_declared",
            kind="scientific",
            description=(
                "The standard state in force is declared; an unspecified state is "
                "reported instead of being fabricated."
            ),
            required=True,
            error_code="invalid_scientific_input",
            evaluated_by="cccp",
        ),
    ),
    completion_checks=(
        CompletionCheck(
            id="weights_policy_recorded",
            description=(
                "The supplied weight form, declared tolerance, supplied sum and the "
                "resulting normalized weights are recorded in provenance."
            ),
            evaluator="cccp.analysis.ensemble.science",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("weight_mode_supplied", "weight_tolerance", "weights_normalized"),
        ),
        CompletionCheck(
            id="weights_normalized",
            description=(
                "The resulting normalized weights sum to 1 within the declared tolerance."
            ),
            evaluator="cccp.analysis.ensemble.science",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("weights_normalized_sum", "weight_tolerance"),
        ),
        CompletionCheck(
            id="ensemble_complete",
            description=(
                "Every declared component contributes a weight and a Gibbs value; a "
                "missing component is reported as incomplete."
            ),
            evaluator="cccp.analysis.ensemble.science",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("components_used", "components_missing"),
        ),
        CompletionCheck(
            id="total_gibbs_finite",
            description=(
                "The ensemble total Gibbs energy is finite and derived from the rank-1 "
                "component and its normalized weight."
            ),
            evaluator="cccp.analysis.ensemble.science",
            required=True,
            failure_error_code="invalid_scientific_input",
            compares=("rank1_component_id", "rank1_weight", "total_gibbs_hartree"),
        ),
    ),
    limitations=(
        Limitation(
            id="supplied_weights_not_rederived",
            text=(
                "The component weights are supplied by the caller and recorded; they are "
                "not re-derived from the Gibbs values or replaced by Boltzmann weights."
            ),
            severity="info",
            applies_to="all ensemble weights",
        ),
        Limitation(
            id="missing_component_blocks_total",
            text=(
                "A component without a weight or Gibbs value makes the ensemble "
                "incomplete; no partial total is presented as the ensemble total."
            ),
            severity="hard_limit",
            applies_to="incomplete component sets",
        ),
        Limitation(
            id="single_temperature_only",
            text=(
                "The result applies to the declared temperature only; a different "
                "temperature requires a new evaluation and provenance record."
            ),
            severity="warning",
            applies_to="all results",
        ),
    ),
    provenance_requirements=ProvenanceRequirements(
        method_identity="not_applicable",
        notes=(
            "Provenance records the temperature, the supplied and normalized standard "
            "state, the supplied weight form and tolerance, the supplied/normalized "
            "weights, the component ids (including missing ones) and the resulting "
            "rank-1 total Gibbs value.",
        ),
    ),
    projections=_ANALYSIS_PROJECTIONS,
)

DOMAIN_DESCRIPTORS: tuple[CapabilityDescriptor, ...] = tuple(
    sorted(
        (_NMR_COMPARE, _NMR_SHIELDING, _THERMOCHEMISTRY_ENSEMBLE),
        key=lambda descriptor: descriptor.capability_id,
    )
)
DOMAIN_CAPABILITY_IDS: tuple[str, ...] = tuple(
    descriptor.capability_id for descriptor in DOMAIN_DESCRIPTORS
)
_DESCRIPTOR_BY_ID: Mapping[str, CapabilityDescriptor] = MappingProxyType(
    {descriptor.capability_id: descriptor for descriptor in DOMAIN_DESCRIPTORS}
)


def domain_registry(resolver: ExecutableResolver | None = None) -> CapabilityRegistry:
    """Return the frozen built-in catalog plus the A-24 domain descriptors."""
    return CapabilityRegistry(
        (*BUILTIN_DESCRIPTORS, *DOMAIN_DESCRIPTORS),
        resolver=resolver,
    )


def domain_catalog(resolver: ExecutableResolver | None = None) -> CapabilityCatalog:
    """Return the deterministically ordered combined catalog."""
    return domain_registry(resolver=resolver).catalog()


def domain_catalog_json(resolver: ExecutableResolver | None = None) -> str:
    """Return the canonical serialized combined catalog."""
    return domain_registry(resolver=resolver).catalog_json()


def describe_domain_family(family: str) -> CapabilityFamilyDescription:
    """Describe one A-24 domain family without inventing a descriptor."""
    normalized = family.strip().lower() if isinstance(family, str) else ""
    capability_ids = tuple(
        capability_id
        for capability_id in DOMAIN_CAPABILITY_IDS
        if capability_id.split(".", 1)[0] == normalized
    )
    if capability_ids:
        return CapabilityFamilyDescription(
            family=normalized,
            described=True,
            supported=True,
            capability_ids=capability_ids,
            reason="family has capabilities in the A-24 domain extension catalog",
        )
    return CapabilityFamilyDescription(
        family=normalized,
        described=False,
        supported=False,
        reason="family is not declared by the A-24 domain extension catalog",
    )


def require_domain_descriptor(capability_id: str) -> CapabilityDescriptor:
    """Return one domain descriptor or raise the registry's typed error."""
    descriptor = _DESCRIPTOR_BY_ID.get(capability_id)
    if descriptor is None:
        raise UnknownCapabilityError(
            f"capability {capability_id!r} is not in the domain extension catalog"
        )
    return descriptor
