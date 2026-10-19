"""Subsampled (display) bounded product (plan todo 32, C06-s2).

Subsampling selects a subset of an existing curve/scatter series.  Per T31
§5.2 it proves **only** two things — every selected point maps back to a unique
original point (``point_id`` + native index), and the declared scientific key
points are retained or explicitly labeled as omitted.  It makes no claim of
full-sequence reconstruction and carries the explicit label that a display
sampling is not the full scientific result (registry §4.6; IS-8; HN-02a §4).

No clustering, dimensionality reduction or other science is recomputed here.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Sequence

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.analysis.bounded.identity import (
    GENERATOR_SUBSAMPLE,
    METHOD_STRIDE,
    BoundedSpec,
    bounded_identity,
)
from cccp.analysis.bounded.manifest import (
    DISPLAY_SAMPLING_NOTE,
    BoundedBlock,
    BoundedProduct,
    KeyPointRetention,
    _ensure_native_source,
)
from cccp.calculation.products.contracts import (
    CurveScatterPayload,
    CurveSeries,
    PointRecord,
    ProductIdentity,
    ScienceProduct,
)
from cccp.calculation.products.curve import build_curve_scatter_product

__all__ = [
    "DEFAULT_KEY_POINTS",
    "KEY_ENDPOINTS",
    "KEY_GLOBAL_MAX",
    "KEY_GLOBAL_MIN",
    "KEY_LOCAL_MIN",
    "find_key_point_indices",
    "select_series",
    "subsample_curve_product",
]

_ALGORITHM_VERSION = "1"

#: Scientific key point selectors (min/max energy, local minima, endpoints).
KEY_GLOBAL_MIN = "global_min_y"
KEY_GLOBAL_MAX = "global_max_y"
KEY_LOCAL_MIN = "local_min_y"
KEY_ENDPOINTS = "endpoints"

DEFAULT_KEY_POINTS: tuple[str, ...] = (KEY_GLOBAL_MIN, KEY_GLOBAL_MAX, KEY_ENDPOINTS)

_OMISSION_REASON = "display_subsampling_excluded_key_point"


def _require_stride(stride: int) -> int:
    if isinstance(stride, bool) or not isinstance(stride, int):
        message = "stride must be an int"
        raise BoundedSchemaError(message)
    if stride < 1:
        message = "stride must be >= 1"
        raise BoundedSchemaError(message)
    return stride


def select_series(product: ScienceProduct, series_id: str | None) -> CurveSeries:
    """Return the requested curve series (single-series default) or raise."""
    payload = product.payload
    if not isinstance(payload, CurveScatterPayload):
        message = "a curve_scatter payload is required"
        raise BoundedSchemaError(message)
    if series_id is None:
        if len(payload.series) != 1:
            message = (
                "a single series is required; pass series_id to select one of "
                f"{len(payload.series)}"
            )
            raise BoundedSchemaError(message)
        return payload.series[0]
    for series in payload.series:
        if series.ref.series_id == series_id:
            return series
    message = f"series_id {series_id!r} not present in the product"
    raise BoundedSchemaError(message)


def find_key_point_indices(
    points: Sequence[PointRecord],
    kinds: Sequence[str],
) -> tuple[tuple[str, int], ...]:
    """Return ``(kind, native_index)`` for each requested scientific key point.

    Only available points participate; a missing point is never treated as a
    value.  ``local_min_y`` uses strict neighbours (a flat region is not a
    minimum); endpoints are kept only when the endpoint point is available.
    """
    available = [(index, point) for index, point in enumerate(points) if point.available]
    by_index = {index: point for index, point in available}
    found: list[tuple[str, int]] = []
    for kind in kinds:
        if kind == KEY_GLOBAL_MIN:
            if available:
                index = min(available, key=lambda item: item[1].y)[0]  # type: ignore[arg-type]
                found.append((kind, index))
        elif kind == KEY_GLOBAL_MAX:
            if available:
                index = max(available, key=lambda item: item[1].y)[0]  # type: ignore[arg-type]
                found.append((kind, index))
        elif kind == KEY_ENDPOINTS:
            for index in (0, len(points) - 1):
                if index in by_index and not any(
                    existing_kind == kind and existing_index == index
                    for existing_kind, existing_index in found
                ):
                    found.append((kind, index))
        elif kind == KEY_LOCAL_MIN:
            for index, point in available:
                if 0 < index < len(points) - 1:
                    prev = points[index - 1]
                    nxt = points[index + 1]
                    if (
                        prev.available
                        and nxt.available
                        and point.y < prev.y  # type: ignore[operator]
                        and point.y < nxt.y  # type: ignore[operator]
                    ):
                        found.append((kind, index))
        else:
            message = f"unknown key point kind {kind!r}"
            raise BoundedSchemaError(message)
    return tuple(found)


def _selected_indices(
    count: int,
    stride: int,
    key_indices: Sequence[tuple[str, int]],
    retain_key_points: bool,
) -> tuple[int, ...]:
    selected = set(range(0, count, stride))
    if retain_key_points:
        selected.update(index for _kind, index in key_indices)
    return tuple(sorted(selected))


def _build_subsampled_series(
    series: CurveSeries,
    points: Sequence[PointRecord],
    selected: Sequence[int],
    artifact: str,
) -> CurveSeries:
    return CurveSeries(
        ref=series.ref,
        points=tuple(_ensure_native_source(points[index], index, artifact) for index in selected),
        source=series.source,
        parameters=series.parameters,
    )


def _retention_records(
    points: Sequence[PointRecord],
    key_indices: Sequence[tuple[str, int]],
    selected: set[int],
    *,
    resolution: int,
) -> tuple[KeyPointRetention, ...]:
    records: list[KeyPointRetention] = []
    for kind, index in key_indices:
        retained = index in selected
        point = points[index]
        records.append(
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
    return tuple(records)


def subsample_curve_product(  # noqa: PLR0913 - explicit scientific options
    product: ScienceProduct,
    *,
    stride: int,
    series_id: str | None = None,
    key_points: Sequence[str] = DEFAULT_KEY_POINTS,
    retain_key_points: bool = True,
) -> BoundedProduct:
    """Build a display-subsampled :class:`BoundedProduct` from a curve series.

    Args:
        product: An available ``curve_scatter`` product (real generator output).
        stride: Keep every ``stride``-th original point.
        series_id: Series to subsample; required when more than one series.
        key_points: Scientific key-point selectors to retain/label.
        retain_key_points: When ``True`` (default) every key point is force-kept;
            when ``False`` a key point not on the stride is instead recorded as
            omitted with an explicit reason (never silently dropped).

    Returns:
        A display-subsampled :class:`BoundedProduct` labeled
        :data:`~cccp.analysis.bounded.manifest.DISPLAY_SAMPLING_NOTE`; each
        selected point maps back to its original ``point_id``/native index.
    """
    stride = _require_stride(stride)
    if not isinstance(product, ScienceProduct) or not product.available or product.payload is None:
        message = "subsample_curve_product requires an available product"
        raise BoundedSchemaError(message)
    base_identity: ProductIdentity = product.identity
    payload = product.payload
    if not isinstance(payload, CurveScatterPayload):
        message = "subsample_curve_product requires a curve_scatter payload"
        raise BoundedSchemaError(message)
    series = select_series(product, series_id)
    points = series.points
    count = len(points)
    kinds = tuple(key_points)
    key_indices = find_key_point_indices(points, kinds)
    selected = _selected_indices(count, stride, key_indices, retain_key_points)
    artifact = series.source.artifact if series.source is not None else ""

    spec = BoundedSpec(
        method=METHOD_STRIDE,
        resolution=stride,
        start=0,
        end=count,
        algorithm_version=_ALGORITHM_VERSION,
        params=(
            ("series_id", series.ref.series_id),
            ("key_points", list(kinds)),
            ("retain_key_points", bool(retain_key_points)),
        ),
    )
    identity = bounded_identity(base_identity, spec, generator=GENERATOR_SUBSAMPLE)

    level_series = _build_subsampled_series(series, points, selected, artifact)
    level_product = build_curve_scatter_product(
        series=[level_series],
        reference_frame=payload.reference_frame,
        input_digest=base_identity.input_digest,
        algorithm=METHOD_STRIDE,
        algorithm_version=_ALGORITHM_VERSION,
        params_digest=spec.params_digest(),
    )
    block = BoundedBlock(
        block_index=0,
        start=0,
        end=count,
        resolution=stride,
        product=level_product,
    )
    retention = _retention_records(
        points,
        key_indices,
        set(selected),
        resolution=stride,
    )
    return BoundedProduct(
        identity=identity,
        base_identity=base_identity,
        spec=spec,
        display_subsampled=True,
        note=DISPLAY_SAMPLING_NOTE,
        blocks=(block,),
        key_points=retention,
    )
