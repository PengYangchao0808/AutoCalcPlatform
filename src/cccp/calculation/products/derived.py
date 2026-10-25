"""Derived-product builder (plan todo 12).

The builder is the *real generating function* registered for
:data:`~cccp.calculation.products.contracts.ProductKind.DERIVED`.  A derived
product records how one product was computed from others: the source mapping
(``SourceRef`` list), each source's version, and the raw I/O plus typed
program-specific parameters that were used.  It is a pure data transform and
never invents a source.

Generation timing (see also ``registry``): after a derivation task (relative
energies / Boltzmann weights / aggregated curves / sampling saturation)
completes, at result-publication time, from the source products — the platform
publishes; cccp owns the science contract.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence

from cccp.calculation.products.contracts import (
    DerivedPayload,
    MissingReason,
    ProductIdentity,
    ProductIODatum,
    ProductKind,
    ProductParameter,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    SourceRef,
    compute_input_digest,
)

__all__ = [
    "GENERATING_FUNCTION",
    "build_derived_product",
]

#: Fully-qualified name of the real generating function (stable identity field).
GENERATING_FUNCTION = "cccp.calculation.products.derived.build_derived_product"


def build_derived_product(
    *,
    sources: Sequence[SourceRef],
    source_versions: Sequence[str] = (),
    parameters: Sequence[ProductParameter] = (),
    raw_io: Sequence[ProductIODatum] = (),
    reference_frame: ReferenceFrame | None = None,
    raw_input: object | None = None,
    input_digest: str | None = None,
    algorithm: str = "derived",
    algorithm_version: str = "1",
    params_digest: str = "",
) -> ScienceProduct:
    """Build a derived :class:`ScienceProduct`.

    Args:
        sources: Source mapping to the products this one derives from.
        source_versions: One version string per source (reproducibility).
        parameters: Typed program-specific parameters kept verbatim.
        raw_io: Raw input/output data kept verbatim.
        reference_frame: Axes/origin default.
        raw_input: Raw input object digested into ``identity.input_digest``.
        input_digest: Precomputed digest; overrides ``raw_input`` when given.
        algorithm: Stable algorithm name for the identity.
        algorithm_version: Algorithm version for the identity.
        params_digest: Optional digest of the generation parameters.

    Returns:
        An ``AVAILABLE`` product (partial when any source lacks a version); an
        explicit ``UNAVAILABLE`` product (``source_missing``) when no source is
        supplied — never an empty success.
    """
    identity = ProductIdentity(
        product_kind=ProductKind.DERIVED,
        generator=GENERATING_FUNCTION,
        input_digest=input_digest
        or (
            compute_input_digest(raw_input)
            if raw_input is not None
            else compute_input_digest([source.to_dict() for source in sources])
        ),
        algorithm=algorithm,
        algorithm_version=algorithm_version,
        params_digest=params_digest,
    )
    if not sources:
        return ScienceProduct.make_unavailable(
            identity=identity,
            missing_reason=MissingReason.SOURCE_MISSING,
            state=ProductState.UNAVAILABLE,
        )
    payload = DerivedPayload(
        sources=tuple(sources),
        source_versions=tuple(source_versions),
        parameters=tuple(parameters),
        raw_io=tuple(raw_io),
        reference_frame=reference_frame if reference_frame is not None else ReferenceFrame(),
    )
    return ScienceProduct.make_available(identity=identity, payload=payload)
