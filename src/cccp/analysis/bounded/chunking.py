"""Lossless bounded chunking (plan todo 32, C06-s2).

Chunking partitions an existing C02 product into bounded blocks *without*
losing the sequence: every block is a real science product built by the
registered generating function, each original item keeps its stable id and
native index, and :func:`~cccp.analysis.bounded.manifest.reconstruct_curve_series`
/ :func:`~cccp.analysis.bounded.manifest.reconstruct_structure_frames` rebuild
the exact original order.  No science is recomputed and no algorithm chain is
added.

Author: QCcalc Team
"""

from __future__ import annotations

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.analysis.bounded.identity import (
    GENERATOR_CHUNK,
    GENERATOR_TRAJECTORY_CHUNK,
    METHOD_CHUNK,
    BoundedSpec,
    bounded_identity,
)
from cccp.analysis.bounded.manifest import (
    BoundedBlock,
    BoundedProduct,
    _ensure_native_source,
)
from cccp.calculation.products.contracts import (
    CurveScatterPayload,
    CurveSeries,
    ProductIdentity,
    ProductKind,
    ScienceProduct,
    StructureTrajectoryPayload,
)
from cccp.calculation.products.curve import build_curve_scatter_product
from cccp.calculation.products.structure import build_structure_trajectory_product

__all__ = [
    "chunk_curve_product",
    "chunk_trajectory_product",
]

_ALGORITHM_VERSION = "1"


def _require_available(product: ScienceProduct, *, action: str) -> ProductIdentity:
    if not isinstance(product, ScienceProduct):
        message = "product must be a ScienceProduct"
        raise BoundedSchemaError(message)
    if not product.available or product.payload is None:
        message = f"{action} requires an available product with a payload"
        raise BoundedSchemaError(message)
    return product.identity


def _require_chunk_size(chunk_size: int) -> int:
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int):
        message = "chunk_size must be an int"
        raise BoundedSchemaError(message)
    if chunk_size < 1:
        message = "chunk_size must be >= 1"
        raise BoundedSchemaError(message)
    return chunk_size


def _select_series(payload: CurveScatterPayload, series_id: str | None) -> CurveSeries:
    if series_id is None:
        if len(payload.series) != 1:
            message = (
                "chunk_curve_product requires a single series; pass series_id to "
                f"select one of {len(payload.series)}"
            )
            raise BoundedSchemaError(message)
        return payload.series[0]
    for series in payload.series:
        if series.ref.series_id == series_id:
            return series
    message = f"series_id {series_id!r} not present in the product"
    raise BoundedSchemaError(message)


def chunk_curve_product(
    product: ScienceProduct,
    *,
    chunk_size: int,
    series_id: str | None = None,
) -> BoundedProduct:
    """Partition a curve/scatter product into lossless bounded blocks.

    Args:
        product: An available ``curve_scatter`` product (real generator output).
        chunk_size: Maximum number of points per block.
        series_id: Series to chunk; required when the product holds more than
            one series.

    Returns:
        A :class:`BoundedProduct` whose blocks each carry at most
        ``chunk_size`` points in original order, with the derived bounded
        identity.  The original ``point_id``/``native_index`` mapping is
        preserved so :func:`~cccp.analysis.bounded.manifest.reconstruct_curve_series`
        is exact.
    """
    chunk_size = _require_chunk_size(chunk_size)
    base_identity = _require_available(product, action="chunk_curve_product")
    payload = product.payload
    if not isinstance(payload, CurveScatterPayload):
        message = "chunk_curve_product requires a curve_scatter payload"
        raise BoundedSchemaError(message)
    series = _select_series(payload, series_id)
    points = series.points
    count = len(points)

    spec = BoundedSpec(
        method=METHOD_CHUNK,
        resolution=chunk_size,
        start=0,
        end=count,
        algorithm_version=_ALGORITHM_VERSION,
        params=(
            ("series_id", series.ref.series_id),
            ("product_kind", ProductKind.CURVE_SCATTER.value),
        ),
    )
    identity = bounded_identity(base_identity, spec, generator=GENERATOR_CHUNK)

    artifact = series.source.artifact if series.source is not None else ""
    blocks: list[BoundedBlock] = []
    for block_index, start in enumerate(range(0, count, chunk_size)):
        end = min(start + chunk_size, count)
        chunk_points = tuple(
            _ensure_native_source(points[index], index, artifact) for index in range(start, end)
        )
        chunk_series = CurveSeries(
            ref=series.ref,
            points=chunk_points,
            source=series.source,
            parameters=series.parameters,
        )
        block_spec = BoundedSpec(
            method=METHOD_CHUNK,
            resolution=chunk_size,
            start=start,
            end=end,
            algorithm_version=_ALGORITHM_VERSION,
            params=(("series_id", series.ref.series_id), ("block_index", block_index)),
        )
        block_product = build_curve_scatter_product(
            series=[chunk_series],
            reference_frame=payload.reference_frame,
            input_digest=base_identity.input_digest,
            algorithm=METHOD_CHUNK,
            algorithm_version=_ALGORITHM_VERSION,
            params_digest=block_spec.params_digest(),
        )
        blocks.append(
            BoundedBlock(
                block_index=block_index,
                start=start,
                end=end,
                resolution=chunk_size,
                product=block_product,
            )
        )

    return BoundedProduct(
        identity=identity,
        base_identity=base_identity,
        spec=spec,
        display_subsampled=False,
        note="",
        blocks=tuple(blocks),
    )


def chunk_trajectory_product(
    product: ScienceProduct,
    *,
    chunk_size: int,
) -> BoundedProduct:
    """Partition a structure/trajectory product into lossless bounded blocks.

    Args:
        product: An available ``structure_trajectory`` product.
        chunk_size: Maximum number of frames per block.

    Returns:
        A :class:`BoundedProduct` whose blocks each carry at most
        ``chunk_size`` frames in original order (``frame_index``/``frame_id``
        preserved), with the derived bounded identity.
    """
    chunk_size = _require_chunk_size(chunk_size)
    base_identity = _require_available(product, action="chunk_trajectory_product")
    payload = product.payload
    if not isinstance(payload, StructureTrajectoryPayload):
        message = "chunk_trajectory_product requires a structure_trajectory payload"
        raise BoundedSchemaError(message)
    frames = payload.frames
    count = len(frames)

    spec = BoundedSpec(
        method=METHOD_CHUNK,
        resolution=chunk_size,
        start=0,
        end=count,
        algorithm_version=_ALGORITHM_VERSION,
        params=(("product_kind", ProductKind.STRUCTURE_TRAJECTORY.value),),
    )
    identity = bounded_identity(base_identity, spec, generator=GENERATOR_TRAJECTORY_CHUNK)

    blocks: list[BoundedBlock] = []
    for block_index, start in enumerate(range(0, count, chunk_size)):
        end = min(start + chunk_size, count)
        block_spec = BoundedSpec(
            method=METHOD_CHUNK,
            resolution=chunk_size,
            start=start,
            end=end,
            algorithm_version=_ALGORITHM_VERSION,
            params=(("product_kind", ProductKind.STRUCTURE_TRAJECTORY.value),),
        )
        block_product = build_structure_trajectory_product(
            frames=frames[start:end],
            topology_source=payload.topology_source,
            coordinate_units=payload.coordinate_units,
            reference_frame=payload.reference_frame,
            atom_order=payload.atom_order,
            input_digest=base_identity.input_digest,
            algorithm=METHOD_CHUNK,
            algorithm_version=_ALGORITHM_VERSION,
            params_digest=block_spec.params_digest(),
        )
        blocks.append(
            BoundedBlock(
                block_index=block_index,
                start=start,
                end=end,
                resolution=chunk_size,
                product=block_product,
            )
        )

    return BoundedProduct(
        identity=identity,
        base_identity=base_identity,
        spec=spec,
        display_subsampled=False,
        note="",
        blocks=tuple(blocks),
    )
