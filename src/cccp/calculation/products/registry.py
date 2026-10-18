"""Self-describing product registry (plan todo 11; consumed by todo 13).

For every science-product kind the registry declares what a caller (Python,
CLI, or the B-side frames adapter) must know to generate and consume it: the
real generating function, generation timing, stable identity rule, raw-input
digest rule, schema reader/writer, the old-version conversion path and the
closed set of missing/failure reasons (review C5).  ``todo 12`` adds a
:class:`ProductSpec` per new kind.

Author: QCcalc Team
"""

from __future__ import annotations

from dataclasses import dataclass

from cccp.calculation.products.contracts import (
    PRODUCT_SCHEMA_VERSION,
    MissingReason,
    ProductKind,
)

__all__ = [
    "PRODUCT_SPECS",
    "ProductSpec",
    "product_spec",
    "product_specs",
]


@dataclass(frozen=True, slots=True)
class ProductSpec:
    """Contract description of one science-product kind (todo 13 handoff)."""

    product_kind: ProductKind
    schema_version: int
    generating_function: str
    generation_timing: str
    stable_identity_rule: str
    raw_input_digest_rule: str
    reader: str
    writer: str
    old_version_conversion: str
    missing_reasons: tuple[MissingReason, ...]


PRODUCT_SPECS: dict[ProductKind, ProductSpec] = {
    ProductKind.STRUCTURE_TRAJECTORY: ProductSpec(
        product_kind=ProductKind.STRUCTURE_TRAJECTORY,
        schema_version=PRODUCT_SCHEMA_VERSION,
        generating_function=(
            "cccp.calculation.products.structure.build_structure_trajectory_product"
        ),
        generation_timing=(
            "After a trajectory-producing task (scan / IRC / optimize / xTB-MD / "
            "path search) completes, at result-publication time, from the task's "
            "TaskResult frame geometry; no re-computation, no subprocess."
        ),
        stable_identity_rule=(
            "ProductIdentity = (product_kind, schema_version, generator, "
            "input_digest, algorithm, algorithm_version, params_digest). "
            "Platform job/account/price identity is excluded."
        ),
        raw_input_digest_rule=(
            "sha256 over the canonical JSON (sorted keys, ASCII, compact "
            "separators) of the raw source (coordinates + symbols + frame order); "
            "mappings are order-independent."
        ),
        reader="cccp.calculation.products.serialize.product_from_dict",
        writer="cccp.calculation.products.serialize.product_to_dict",
        old_version_conversion=(
            "schema_version 0 -> 1 via "
            "cccp.calculation.products.serialize.migrate_v0_to_v1: derives "
            "atom_uid as element:rank, declares angstrom coordinates and a "
            "legacy_v0 topology source, recording every decision in "
            "ScienceProduct.migrations."
        ),
        missing_reasons=(
            MissingReason.SOURCE_MISSING,
            MissingReason.PARTIAL_SOURCE,
            MissingReason.PARSE_FAILURE,
            MissingReason.UPSTREAM_UNAVAILABLE,
            MissingReason.NOT_PREPARED,
            MissingReason.UNSUPPORTED,
            MissingReason.MISSING_FRAME_ID,
            MissingReason.MISSING_ATOM_UID,
            MissingReason.MISSING_ELEMENT,
            MissingReason.MISSING_COORDINATES,
        ),
    ),
    ProductKind.CURVE_SCATTER: ProductSpec(
        product_kind=ProductKind.CURVE_SCATTER,
        schema_version=PRODUCT_SCHEMA_VERSION,
        generating_function=("cccp.calculation.products.curve.build_curve_scatter_product"),
        generation_timing=(
            "After a curve-producing task (scan / IRC / sampling trajectory) "
            "completes, at result-publication time, from the task's numeric "
            "series; no re-computation, no subprocess."
        ),
        stable_identity_rule=(
            "ProductIdentity = (product_kind, schema_version, generator, "
            "input_digest, algorithm, algorithm_version, params_digest); each "
            "series/point adds a stable id (series_stable_id / point_stable_id) "
            "and a SourceRef.  Platform job/account/price identity is excluded."
        ),
        raw_input_digest_rule=(
            "sha256 over the canonical JSON (sorted keys, ASCII, compact "
            "separators) of the raw source series (x/y plus source order); "
            "mappings are order-independent."
        ),
        reader="cccp.calculation.products.serialize.product_from_dict",
        writer="cccp.calculation.products.serialize.product_to_dict",
        old_version_conversion=(
            "none: the curve/scatter kind was introduced with the versioned "
            "contract, so no pre-versioning (v0) documents exist; a v0 document "
            "is rejected as corrupt by migrate_v0_to_v1."
        ),
        missing_reasons=(
            MissingReason.SOURCE_MISSING,
            MissingReason.PARTIAL_SOURCE,
            MissingReason.PARSE_FAILURE,
            MissingReason.UPSTREAM_UNAVAILABLE,
            MissingReason.NOT_PREPARED,
            MissingReason.UNSUPPORTED,
            MissingReason.MISSING_SERIES,
            MissingReason.MISSING_POINT_VALUES,
        ),
    ),
    ProductKind.VIBRATION: ProductSpec(
        product_kind=ProductKind.VIBRATION,
        schema_version=PRODUCT_SCHEMA_VERSION,
        generating_function=("cccp.calculation.products.vibration.build_vibration_product"),
        generation_timing=(
            "After a frequency task completes, at result-publication time, from "
            "the parsed FrequencyAnalysis (mode_frequencies / mode_vectors / "
            "mode_ir_intensities); no second parse, no subprocess."
        ),
        stable_identity_rule=(
            "ProductIdentity = (product_kind, schema_version, generator, "
            "input_digest, algorithm, algorithm_version, params_digest).  Each "
            "mode keeps its ORCA native mode index (never renumbered, zero "
            "modes retained) and displacement rows align with atom_order "
            "(atom_uid identity)."
        ),
        raw_input_digest_rule=(
            "sha256 over the canonical JSON (sorted keys, ASCII, compact "
            "separators) of the raw source mode maps (frequency + vectors keyed "
            "by native mode index)."
        ),
        reader="cccp.calculation.products.serialize.product_from_dict",
        writer="cccp.calculation.products.serialize.product_to_dict",
        old_version_conversion=(
            "none: the vibration kind was introduced with the versioned "
            "contract, so no pre-versioning (v0) documents exist; a v0 document "
            "is rejected as corrupt by migrate_v0_to_v1."
        ),
        missing_reasons=(
            MissingReason.SOURCE_MISSING,
            MissingReason.PARTIAL_SOURCE,
            MissingReason.PARSE_FAILURE,
            MissingReason.UPSTREAM_UNAVAILABLE,
            MissingReason.NOT_PREPARED,
            MissingReason.UNSUPPORTED,
            MissingReason.MISSING_FREQUENCY,
            MissingReason.MISSING_DISPLACEMENTS,
            MissingReason.MISSING_ATOM_UID,
        ),
    ),
    ProductKind.DERIVED: ProductSpec(
        product_kind=ProductKind.DERIVED,
        schema_version=PRODUCT_SCHEMA_VERSION,
        generating_function=("cccp.calculation.products.derived.build_derived_product"),
        generation_timing=(
            "After a derivation task (relative energies / Boltzmann weights / "
            "aggregated curves / sampling saturation) completes, at "
            "result-publication time, from the source products; no "
            "re-computation, no subprocess."
        ),
        stable_identity_rule=(
            "ProductIdentity = (product_kind, schema_version, generator, "
            "input_digest, algorithm, algorithm_version, params_digest); the "
            "payload also records the source mapping (SourceRef list) and each "
            "source version.  Platform identity is excluded."
        ),
        raw_input_digest_rule=(
            "sha256 over the canonical JSON (sorted keys, ASCII, compact "
            "separators) of the raw source references plus kept parameters."
        ),
        reader="cccp.calculation.products.serialize.product_from_dict",
        writer="cccp.calculation.products.serialize.product_to_dict",
        old_version_conversion=(
            "none: the derived kind was introduced with the versioned contract, "
            "so no pre-versioning (v0) documents exist; a v0 document is "
            "rejected as corrupt by migrate_v0_to_v1."
        ),
        missing_reasons=(
            MissingReason.SOURCE_MISSING,
            MissingReason.PARTIAL_SOURCE,
            MissingReason.PARSE_FAILURE,
            MissingReason.UPSTREAM_UNAVAILABLE,
            MissingReason.NOT_PREPARED,
            MissingReason.UNSUPPORTED,
        ),
    ),
}


def product_spec(kind: ProductKind) -> ProductSpec:
    """Return the contract description for ``kind``.

    Raises:
        KeyError: When no spec is registered (a contract bug — every kind in
            :class:`ProductKind` must register one).
    """
    return PRODUCT_SPECS[ProductKind(kind)]


def product_specs() -> tuple[ProductSpec, ...]:
    """All registered product specs, in :class:`ProductKind` order."""
    return tuple(PRODUCT_SPECS[kind] for kind in ProductKind)
