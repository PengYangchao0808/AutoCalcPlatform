"""Multi-resolution bounded product (plan todo 32, C06-s2).

A multi-resolution product publishes several bounded display levels of one
curve/scatter series at once (e.g. strides 1/10/100).  Each level is a real
C02 science product with a distinct series id whose points keep their original
``point_id``/native index; every level's key points are retained or explicitly
labeled.  The whole product is labeled as a display projection, never the full
scientific result.

No clustering or dimensionality reduction is recomputed.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.analysis.bounded.identity import (
    GENERATOR_MULTIRESOLUTION,
    METHOD_MULTIRESOLUTION,
    MULTIRESOLUTION_ALGORITHM_VERSION,
    BoundedSpec,
    bounded_identity,
    multiresolution_block_spec,
    source_identity_digest,
)
from cccp.analysis.bounded.manifest import (
    DISPLAY_SAMPLING_NOTE,
    BoundedBlock,
    BoundedProduct,
    KeyPointRetention,
    _ensure_native_source,
)
from cccp.analysis.bounded.subsampling import (
    DEFAULT_KEY_POINTS,
    _selected_indices,
    find_key_point_indices,
    select_series,
)
from cccp.calculation.products.contracts import (
    CurveScatterPayload,
    CurveSeries,
    ProductIdentity,
    ScienceProduct,
    SeriesRef,
)
from cccp.calculation.products.curve import build_curve_scatter_product

__all__ = [
    "build_multiresolution_product",
]

_ALGORITHM_VERSION = MULTIRESOLUTION_ALGORITHM_VERSION
_OMISSION_REASON = "display_subsampling_excluded_key_point"


def _require_resolutions(resolutions: Sequence[int]) -> tuple[int, ...]:
    normalised: list[int] = []
    for value in resolutions:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            message = "every resolution must be an int >= 1"
            raise BoundedSchemaError(message)
        normalised.append(value)
    unique = tuple(sorted(set(normalised)))
    if not unique:
        message = "at least one resolution is required"
        raise BoundedSchemaError(message)
    return unique


def _level_ref(base: SeriesRef, resolution: int) -> SeriesRef:
    label = f"{base.label} (r={resolution})" if base.label else f"r={resolution}"
    return SeriesRef(
        series_id=f"{base.series_id}:res:{resolution}",
        x_unit=base.x_unit,
        y_unit=base.y_unit,
        reference_frame=base.reference_frame,
        label=label,
    )


def build_multiresolution_product(  # noqa: PLR0913 - explicit scientific options
    product: ScienceProduct,
    *,
    resolutions: Sequence[int],
    series_id: str | None = None,
    key_points: Sequence[str] = DEFAULT_KEY_POINTS,
    retain_key_points: bool = True,
) -> BoundedProduct:
    """Build a multi-resolution display :class:`BoundedProduct`.

    Args:
        product: An available ``curve_scatter`` product (real generator output).
        resolutions: Display strides, one per level (deduplicated + ascending).
        series_id: Series to project; required when more than one series.
        key_points: Scientific key-point selectors retained/labeled per level.
        retain_key_points: Force-keep key points on every level (``True``) or
            record the ones not on the stride as explicitly omitted (``False``).

    Returns:
        A display-subsampled :class:`BoundedProduct` with one block per
        resolution; the derived identity covers the full range and the level
        set, so it differs from a single-resolution product.
    """
    levels = _require_resolutions(resolutions)
    if not isinstance(product, ScienceProduct) or not product.available or product.payload is None:
        message = "build_multiresolution_product requires an available product"
        raise BoundedSchemaError(message)
    base_identity: ProductIdentity = product.identity
    source_digest = source_identity_digest(base_identity)
    payload = product.payload
    if not isinstance(payload, CurveScatterPayload):
        message = "build_multiresolution_product requires a curve_scatter payload"
        raise BoundedSchemaError(message)
    series = select_series(product, series_id)
    points = series.points
    count = len(points)
    kinds = tuple(key_points)
    key_indices = find_key_point_indices(points, kinds)
    artifact = series.source.artifact if series.source is not None else ""

    spec = BoundedSpec(
        method=METHOD_MULTIRESOLUTION,
        resolution=levels[0],
        start=0,
        end=count,
        algorithm_version=_ALGORITHM_VERSION,
        params=(
            ("series_id", series.ref.series_id),
            ("resolutions", list(levels)),
            ("key_points", list(kinds)),
            ("retain_key_points", bool(retain_key_points)),
        ),
        source_identity_digest=source_digest,
    )
    identity = bounded_identity(base_identity, spec, generator=GENERATOR_MULTIRESOLUTION)

    blocks: list[BoundedBlock] = []
    retention: list[KeyPointRetention] = []
    for level_index, resolution in enumerate(levels):
        selected = _selected_indices(count, resolution, key_indices, retain_key_points)
        level_series = CurveSeries(
            ref=_level_ref(series.ref, resolution),
            points=tuple(
                _ensure_native_source(points[index], index, artifact) for index in selected
            ),
            source=series.source,
            parameters=series.parameters,
        )
        block_spec = multiresolution_block_spec(spec, level_index=level_index)
        level_product = build_curve_scatter_product(
            series=[level_series],
            reference_frame=payload.reference_frame,
            input_digest=base_identity.input_digest,
            algorithm=METHOD_MULTIRESOLUTION,
            algorithm_version=_ALGORITHM_VERSION,
            params_digest=block_spec.params_digest(),
        )
        blocks.append(
            BoundedBlock(
                block_index=level_index,
                start=0,
                end=count,
                resolution=resolution,
                product=level_product,
                source_identity_digest=source_digest,
            )
        )
        selected_set = set(selected)
        for kind, index in key_indices:
            retained = index in selected_set
            point = points[index]
            retention.append(
                KeyPointRetention(
                    kind=kind,
                    native_index=index,
                    retained=retained,
                    resolution=resolution,
                    x=point.x,
                    y=point.y,
                    reason="" if retained else _OMISSION_REASON,
                )
            )

    return BoundedProduct(
        identity=identity,
        base_identity=base_identity,
        spec=spec,
        display_subsampled=True,
        note=DISPLAY_SAMPLING_NOTE,
        blocks=tuple(blocks),
        key_points=tuple(retention),
    )
