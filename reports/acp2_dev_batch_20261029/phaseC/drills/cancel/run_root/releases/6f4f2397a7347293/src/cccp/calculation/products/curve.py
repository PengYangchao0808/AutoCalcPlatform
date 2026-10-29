"""Curve/scatter product builder (plan todo 12).

The builder is the *real generating function* registered for
:data:`~cccp.calculation.products.contracts.ProductKind.CURVE_SCATTER`.  It is
a pure data transform: it consumes already-computed series/points plus the
raw-input digest and produces a versioned ``ScienceProduct``.  No I/O, no
re-computation, and a missing point never becomes a zero value.

Generation timing (see also ``registry``): after a curve-producing task
(scan / IRC / sampling trajectory) completes, at result-publication time,
from the task's numeric series — the platform publishes; cccp owns the
science contract.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence

from cccp.calculation.products.contracts import (
    CurveScatterPayload,
    CurveSeries,
    MissingReason,
    ProductIdentity,
    ProductKind,
    ProductState,
    ReferenceFrame,
    ScienceProduct,
    compute_input_digest,
)

__all__ = [
    "GENERATING_FUNCTION",
    "build_curve_scatter_product",
]

#: Fully-qualified name of the real generating function (stable identity field).
GENERATING_FUNCTION = "cccp.calculation.products.curve.build_curve_scatter_product"


def build_curve_scatter_product(
    *,
    series: Sequence[CurveSeries],
    reference_frame: ReferenceFrame | None = None,
    raw_input: object | None = None,
    input_digest: str | None = None,
    algorithm: str = "curve_scatter",
    algorithm_version: str = "1",
    params_digest: str = "",
) -> ScienceProduct:
    """Build a curve/scatter :class:`ScienceProduct`.

    Args:
        series: Series in original order; each keeps its x/y unit and points.
        reference_frame: Product-wide axes/origin default.
        raw_input: Raw input object digested into ``identity.input_digest``.
        input_digest: Precomputed digest; overrides ``raw_input`` when given.
        algorithm: Stable algorithm name for the identity.
        algorithm_version: Algorithm version for the identity.
        params_digest: Optional digest of the generation parameters.

    Returns:
        An ``AVAILABLE`` product when at least one series is supplied (partial
        when a point/series carries a missing reason); an explicit
        ``UNAVAILABLE`` product (``source_missing``) otherwise.
    """
    identity = ProductIdentity(
        product_kind=ProductKind.CURVE_SCATTER,
        generator=GENERATING_FUNCTION,
        input_digest=input_digest
        or (
            compute_input_digest(raw_input)
            if raw_input is not None
            else compute_input_digest([entry.to_dict() for entry in series])
        ),
        algorithm=algorithm,
        algorithm_version=algorithm_version,
        params_digest=params_digest,
    )
    if not series:
        return ScienceProduct.make_unavailable(
            identity=identity,
            missing_reason=MissingReason.SOURCE_MISSING,
            state=ProductState.UNAVAILABLE,
        )
    payload = CurveScatterPayload(
        series=tuple(series),
        reference_frame=reference_frame if reference_frame is not None else ReferenceFrame(),
    )
    return ScienceProduct.make_available(identity=identity, payload=payload)
