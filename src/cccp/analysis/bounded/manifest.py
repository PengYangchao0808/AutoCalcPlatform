"""Bounded-product manifest (plan todo 32, C06-s2).

A :class:`BoundedProduct` is the *index* over a lossless/chunked or
subsampled/multi-resolution projection of one C02 science product.  Every
block is a real :class:`~cccp.calculation.products.contracts.ScienceProduct`
produced by the registered generating functions and read back through the
single C02 reader — this module adds only the bounded manifest, never a second
payload/envelope stack (registry §4.6; HN-02a §4).

Invariants enforced here:

* block indices are contiguous ``0..n-1`` and every ``start <= end``;
* a chunked (lossless) manifest preserves the original point/frame order, so
  :func:`reconstruct_curve_series` / :func:`reconstruct_structure_frames`
  rebuild the exact original sequence;
* a subsampled manifest keeps the original ``point_id`` plus the native index
  in ``PointRecord.source`` so a selected point can always be mapped back, and
  carries an explicit "display sampling is not the full scientific result"
  label.

Author: QCcalc Team
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from cccp.analysis.bounded.errors import BoundedSchemaError
from cccp.analysis.bounded.identity import (
    BOUNDED_IDENTITY_RULE_VERSION,
    BOUNDED_SCHEMA_VERSION,
    LEGACY_BOUNDED_IDENTITY_RULE_VERSION,
    LEGACY_BOUNDED_SCHEMA_VERSION,
    BoundedSpec,
    source_identity_digest,
)
from cccp.calculation.contracts import (
    JsonObject,
    parse_bool_strict,
    parse_float_strict,
    parse_int_strict,
    parse_str_strict,
)
from cccp.calculation.products.contracts import (
    CurveScatterPayload,
    CurveSeries,
    FrameRecord,
    PointRecord,
    ProductIdentity,
    ScienceProduct,
    SourceRef,
    StructureTrajectoryPayload,
)
from cccp.calculation.products.serialize import product_from_dict, product_to_dict

__all__ = [
    "DISPLAY_SAMPLING_NOTE",
    "BoundedBlock",
    "BoundedProduct",
    "KeyPointRetention",
    "native_index_of",
    "reconstruct_curve_series",
    "reconstruct_structure_frames",
    "selected_point_mapping",
]

#: Explicit label that a subsampled/multi-resolution product is a display
#: projection, never the full scientific result (registry §4.6; IS-8).
DISPLAY_SAMPLING_NOTE = (
    "display sampling is not the full scientific result "
    "(显示抽样 ≠ 全量科学结果); selected points map back to original "
    "point_id/native_index and key points are retained or explicitly labeled"
)

_NATIVE_INDEX_MARKER = ":point:"


def native_index_of(point: PointRecord) -> int:
    """Return the original (native) series index of ``point``.

    Prefers the C02 ``SourceRef.native_index``; falls back to the
    ``<series_id>:point:<i>`` stable-id suffix.  Raises
    :class:`BoundedSchemaError` when neither is available — a bounded product
    must never lose the mapping back to the original sequence.
    """
    if point.source is not None and point.source.native_index is not None:
        return int(point.source.native_index)
    point_id = point.point_id
    marker = point_id.rfind(_NATIVE_INDEX_MARKER)
    if marker != -1:
        suffix = point_id[marker + len(_NATIVE_INDEX_MARKER) :]
        if suffix.isdigit():
            return int(suffix)
    message = f"point {point.point_id!r} carries no original native index"
    raise BoundedSchemaError(message)


def _ensure_native_source(point: PointRecord, index: int, artifact: str) -> PointRecord:
    """Return ``point`` verbatim, guaranteeing a native-index ``source``."""
    if point.source is not None and point.source.native_index is not None:
        return point
    source = SourceRef(source_kind="curve_point", artifact=artifact, native_index=index)
    return PointRecord(
        point_id=point.point_id,
        x=point.x,
        y=point.y,
        source=source,
        missing_reason=point.missing_reason,
    )


def _curve_series_of(product: ScienceProduct) -> CurveSeries:
    """Return the single curve series of ``product`` or raise."""
    payload = product.payload
    if not isinstance(payload, CurveScatterPayload):
        message = f"expected a curve_scatter payload, got {type(payload).__name__}"
        raise BoundedSchemaError(message)
    if len(payload.series) != 1:
        message = (
            "bounded curve operations require exactly one series per product; "
            f"got {len(payload.series)}"
        )
        raise BoundedSchemaError(message)
    return payload.series[0]


def _trajectory_payload_of(product: ScienceProduct) -> StructureTrajectoryPayload:
    """Return the structure/trajectory payload of ``product`` or raise."""
    payload = product.payload
    if not isinstance(payload, StructureTrajectoryPayload):
        message = f"expected a structure_trajectory payload, got {type(payload).__name__}"
        raise BoundedSchemaError(message)
    return payload


def _payload_item_count(product: ScienceProduct) -> int:
    payload = product.payload
    if isinstance(payload, CurveScatterPayload):
        return sum(len(series.points) for series in payload.series)
    if isinstance(payload, StructureTrajectoryPayload):
        return len(payload.frames)
    return 0


@dataclass(frozen=True, slots=True)
class KeyPointRetention:
    """Retention record for one scientific key point under a bounded projection.

    ``retained`` is ``True`` when the key point is present in the projection at
    its original ``native_index``; when ``False`` the explicit ``reason``
    records why it was omitted (a key point is never silently dropped).
    """

    kind: str
    native_index: int
    retained: bool
    resolution: int | None = None
    x: float | None = None
    y: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            message = "key point kind must be a non-empty string"
            raise BoundedSchemaError(message)
        if isinstance(self.native_index, bool) or not isinstance(self.native_index, int):
            message = "key point native_index must be an int"
            raise BoundedSchemaError(message)
        if not self.retained and not self.reason:
            message = "an omitted key point must record an explicit reason"
            raise BoundedSchemaError(message)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        payload: JsonObject = {
            "kind": self.kind,
            "native_index": self.native_index,
            "retained": self.retained,
        }
        if self.resolution is not None:
            payload["resolution"] = self.resolution
        if self.x is not None:
            payload["x"] = self.x
        if self.y is not None:
            payload["y"] = self.y
        if self.reason:
            payload["reason"] = self.reason
        return payload

    @classmethod
    def from_dict(cls, payload: object | None) -> KeyPointRetention:
        """Parse strictly; a malformed record raises."""
        if not isinstance(payload, Mapping):
            message = "key_points entry must be a mapping"
            raise BoundedSchemaError(message)
        kind = parse_str_strict(payload, "kind")
        native_index = parse_int_strict(payload, "native_index")
        retained = parse_bool_strict(payload, "retained")
        if kind is None or native_index is None or retained is None:
            message = "key_points entry requires kind/native_index/retained"
            raise BoundedSchemaError(message)
        return cls(
            kind=kind,
            native_index=native_index,
            retained=retained,
            resolution=parse_int_strict(payload, "resolution"),
            x=parse_float_strict(payload, "x"),
            y=parse_float_strict(payload, "y"),
            reason=parse_str_strict(payload, "reason") or "",
        )


@dataclass(frozen=True, slots=True)
class BoundedBlock:
    """One bounded block: an original-index range plus its real C02 product.

    ``source_identity_digest`` mirrors the full source identity folded into the
    block's own derived product identity (identity rule v2).  It is empty only
    on historical (v1) manifests, which remain readable but are not adoptable
    as current cache/recovery identities.
    """

    block_index: int
    start: int
    end: int
    resolution: int
    product: ScienceProduct
    source_identity_digest: str = ""

    def __post_init__(self) -> None:
        for label, value in (
            ("block_index", self.block_index),
            ("start", self.start),
            ("end", self.end),
            ("resolution", self.resolution),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                message = f"block.{label} must be an int"
                raise BoundedSchemaError(message)
        if self.block_index < 0 or self.start < 0 or self.end < self.start:
            message = "block requires 0 <= block_index and 0 <= start <= end"
            raise BoundedSchemaError(message)
        if self.resolution < 1:
            message = "block.resolution must be >= 1"
            raise BoundedSchemaError(message)
        if not isinstance(self.product, ScienceProduct):
            message = "block.product must be a ScienceProduct"
            raise BoundedSchemaError(message)
        if not isinstance(self.source_identity_digest, str):
            message = "block.source_identity_digest must be a string"
            raise BoundedSchemaError(message)
        object.__setattr__(self, "source_identity_digest", str(self.source_identity_digest).strip())

    def item_count(self) -> int:
        """Number of points/frames actually carried by the block's product."""
        return _payload_item_count(self.product)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (block product via the C02 writer)."""
        payload: JsonObject = {
            "block_index": self.block_index,
            "start": self.start,
            "end": self.end,
            "resolution": self.resolution,
            "product": product_to_dict(self.product),
        }
        if self.source_identity_digest:
            payload["source_identity_digest"] = self.source_identity_digest
        return payload

    @classmethod
    def from_dict(cls, payload: object | None) -> BoundedBlock:
        """Parse strictly (block product via the single C02 reader)."""
        if not isinstance(payload, Mapping):
            message = "block entry must be a mapping"
            raise BoundedSchemaError(message)
        for label in ("block_index", "start", "end", "resolution"):
            if parse_int_strict(payload, label) is None:
                message = f"block.{label} is required"
                raise BoundedSchemaError(message)
        raw_product = payload.get("product")
        if not isinstance(raw_product, Mapping):
            message = "block.product is required"
            raise BoundedSchemaError(message)
        return cls(
            block_index=parse_int_strict(payload, "block_index"),  # type: ignore[arg-type]
            start=parse_int_strict(payload, "start"),  # type: ignore[arg-type]
            end=parse_int_strict(payload, "end"),  # type: ignore[arg-type]
            resolution=parse_int_strict(payload, "resolution"),  # type: ignore[arg-type]
            product=product_from_dict(raw_product),
            source_identity_digest=parse_str_strict(payload, "source_identity_digest") or "",
        )


@dataclass(frozen=True, slots=True)
class BoundedProduct:
    """Manifest of a bounded projection over one base science product.

    ``blocks`` are the bounded pieces (each a real C02 product); ``identity``
    is the derived bounded identity; ``base_identity`` is the source product's
    identity; ``display_subsampled`` plus ``note`` label a display sampling.

    ``schema_version`` 2 uses identity rule v2 (the full source identity is
    part of the derived identity); schema version 1 is the read-only legacy
    shape.  Historical manifests stay readable and round-trip byte-equivalently,
    but :meth:`is_cache_adoptable` is ``False`` for them so they are never
    reused as current cache/recovery identities (D10).
    """

    identity: ProductIdentity
    base_identity: ProductIdentity
    spec: BoundedSpec
    display_subsampled: bool
    note: str
    blocks: tuple[BoundedBlock, ...] = ()
    key_points: tuple[KeyPointRetention, ...] = ()
    schema_version: int = BOUNDED_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.identity, ProductIdentity):
            message = "identity must be a ProductIdentity"
            raise BoundedSchemaError(message)
        if not isinstance(self.base_identity, ProductIdentity):
            message = "base_identity must be a ProductIdentity"
            raise BoundedSchemaError(message)
        if not isinstance(self.spec, BoundedSpec):
            message = "spec must be a BoundedSpec"
            raise BoundedSchemaError(message)
        object.__setattr__(self, "schema_version", int(self.schema_version))
        if self.schema_version not in (
            LEGACY_BOUNDED_SCHEMA_VERSION,
            BOUNDED_SCHEMA_VERSION,
        ):
            message = (
                f"BoundedProduct.schema_version {self.schema_version} is not "
                f"{LEGACY_BOUNDED_SCHEMA_VERSION} (legacy) or the current bounded "
                f"contract version {BOUNDED_SCHEMA_VERSION}"
            )
            raise BoundedSchemaError(message)
        object.__setattr__(self, "blocks", tuple(self.blocks))
        object.__setattr__(self, "key_points", tuple(self.key_points))
        object.__setattr__(self, "display_subsampled", bool(self.display_subsampled))
        object.__setattr__(self, "note", str(self.note))
        if self.display_subsampled and not self.note:
            message = "a subsampled product must carry the explicit display label"
            raise BoundedSchemaError(message)
        expected_source_digest = (
            source_identity_digest(self.base_identity)
            if self.schema_version == BOUNDED_SCHEMA_VERSION
            else ""
        )
        expected_rule = (
            BOUNDED_IDENTITY_RULE_VERSION
            if self.schema_version == BOUNDED_SCHEMA_VERSION
            else LEGACY_BOUNDED_IDENTITY_RULE_VERSION
        )
        if self.spec.identity_rule_version != expected_rule:
            message = (
                f"bounded schema_version {self.schema_version} requires identity rule "
                f"{expected_rule}; spec carries rule {self.spec.identity_rule_version}"
            )
            raise BoundedSchemaError(message)
        if self.spec.source_identity_digest != expected_source_digest:
            message = (
                "spec.source_identity_digest disagrees with the full base identity "
                f"({self.spec.source_identity_digest!r} != {expected_source_digest!r})"
            )
            raise BoundedSchemaError(message)
        if self.identity.product_kind is not self.base_identity.product_kind:
            message = "bounded identity product_kind must match the base identity"
            raise BoundedSchemaError(message)
        if self.identity.input_digest != self.base_identity.input_digest:
            message = "bounded identity input_digest must be the source input digest"
            raise BoundedSchemaError(message)
        if self.identity.params_digest != self.spec.params_digest():
            message = "bounded identity params_digest disagrees with the bounded spec"
            raise BoundedSchemaError(message)
        for expected, block in enumerate(self.blocks):
            if block.block_index != expected:
                message = (
                    f"block indices must be contiguous 0..n-1; expected {expected}, "
                    f"got {block.block_index}"
                )
                raise BoundedSchemaError(message)
            if block.start > block.end:
                message = f"block {block.block_index} has start > end"
                raise BoundedSchemaError(message)
            if block.source_identity_digest != expected_source_digest:
                message = (
                    f"block {block.block_index} source_identity_digest disagrees with "
                    "the manifest source identity"
                )
                raise BoundedSchemaError(message)
            block_identity = block.product.identity
            if block_identity.product_kind is not self.base_identity.product_kind:
                message = f"block {block.block_index} product_kind must match the base identity"
                raise BoundedSchemaError(message)
            if block_identity.input_digest != self.base_identity.input_digest:
                message = f"block {block.block_index} input_digest must be the source input digest"
                raise BoundedSchemaError(message)

    def is_cache_adoptable(self) -> bool:
        """Whether this manifest may be reused as a *current* derived identity.

        Historical (schema version 1) manifests lack the full source-identity
        basis, so they read/round-trip but must never be adopted as a new
        cache/recovery result; rebuild rebuildable products under version 2
        instead of deleting the scientific original.
        """
        if self.schema_version != BOUNDED_SCHEMA_VERSION:
            return False
        if self.spec.identity_rule_version != BOUNDED_IDENTITY_RULE_VERSION:
            return False
        expected = source_identity_digest(self.base_identity)
        if self.spec.source_identity_digest != expected:
            return False
        return all(block.source_identity_digest == expected for block in self.blocks)

    def max_block_size(self) -> int:
        """Maximum number of items (points/frames) carried by any block."""
        return max((block.item_count() for block in self.blocks), default=0)

    def total_items(self) -> int:
        """Total number of items across all blocks."""
        return sum(block.item_count() for block in self.blocks)

    def covered_range(self) -> tuple[int, int]:
        """Half-open original-index range actually covered by the blocks."""
        if not self.blocks:
            return (self.spec.start, self.spec.start)
        return (self.blocks[0].start, self.blocks[-1].end)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "schema_version": self.schema_version,
            "bounded_identity": self.identity.to_dict(),
            "base_identity": self.base_identity.to_dict(),
            "spec": self.spec.to_dict(),
            "display_subsampled": self.display_subsampled,
            "note": self.note,
            "blocks": [block.to_dict() for block in self.blocks],
            "key_points": [key.to_dict() for key in self.key_points],
        }

    @classmethod
    def from_dict(cls, payload: object | None) -> BoundedProduct:
        """Parse strictly; malformed manifests raise :class:`BoundedSchemaError`."""
        if not isinstance(payload, Mapping):
            message = "bounded product must be a mapping"
            raise BoundedSchemaError(message)
        version = parse_int_strict(payload, "schema_version")
        if version is None:
            message = "bounded product.schema_version is required"
            raise BoundedSchemaError(message)
        if version not in (
            LEGACY_BOUNDED_SCHEMA_VERSION,
            BOUNDED_SCHEMA_VERSION,
        ):
            message = (
                f"unsupported bounded schema_version {version}; current is "
                f"{BOUNDED_SCHEMA_VERSION} (legacy {LEGACY_BOUNDED_SCHEMA_VERSION})"
            )
            raise BoundedSchemaError(message)
        raw_identity = payload.get("bounded_identity")
        raw_base = payload.get("base_identity")
        if not isinstance(raw_identity, Mapping) or not isinstance(raw_base, Mapping):
            message = "bounded_identity and base_identity are required"
            raise BoundedSchemaError(message)
        raw_blocks = payload.get("blocks")
        if raw_blocks is None:
            raw_blocks = []
        if not isinstance(raw_blocks, list):
            message = "blocks must be a list"
            raise BoundedSchemaError(message)
        raw_keys = payload.get("key_points")
        if raw_keys is None:
            raw_keys = []
        if not isinstance(raw_keys, list):
            message = "key_points must be a list"
            raise BoundedSchemaError(message)
        display = parse_bool_strict(payload, "display_subsampled")
        return cls(
            identity=ProductIdentity.from_dict(raw_identity),
            base_identity=ProductIdentity.from_dict(raw_base),
            spec=BoundedSpec.from_dict(payload.get("spec")),
            display_subsampled=bool(display),
            note=parse_str_strict(payload, "note") or "",
            blocks=tuple(BoundedBlock.from_dict(entry) for entry in raw_blocks),
            key_points=tuple(KeyPointRetention.from_dict(entry) for entry in raw_keys),
            schema_version=version,
        )


def _curve_points(bounded: BoundedProduct) -> tuple[tuple[int, CurveSeries, PointRecord], ...]:
    """Return ``(native_index, series, point)`` for every curve point, sorted.

    A point is keyed by ``(series_id, native_index)`` so a multi-resolution
    manifest (one distinct series per level, overlapping indices) does not
    collide; the lossless/mapping helpers below require a single series.
    """
    entries: list[tuple[int, CurveSeries, PointRecord]] = []
    seen: set[tuple[str, int]] = set()
    series_ids: set[str] = set()
    for block in bounded.blocks:
        series = _curve_series_of(block.product)
        series_ids.add(series.ref.series_id)
        for point in series.points:
            index = native_index_of(point)
            key = (series.ref.series_id, index)
            if key in seen:
                message = f"duplicate point {key} across blocks"
                raise BoundedSchemaError(message)
            seen.add(key)
            entries.append((index, series, point))
    if len(series_ids) > 1:
        message = (
            "this helper requires a single series across blocks; the manifest "
            f"holds {len(series_ids)} series (use block products for per-level access)"
        )
        raise BoundedSchemaError(message)
    entries.sort(key=lambda item: item[0])
    return tuple(entries)


def reconstruct_curve_series(bounded: BoundedProduct) -> CurveSeries:
    """Losslessly rebuild the full original series from a chunked manifest.

    Only valid for a lossless (chunked) projection: the native indices must be
    exactly ``0..N-1`` (100% coverage).  A subsampled manifest fails here — use
    :func:`selected_point_mapping` for that case (separate assertions, per
    T31 §5.2).
    """
    if bounded.display_subsampled:
        message = (
            "reconstruct_curve_series requires a lossless (chunked) manifest; "
            "a display-subsampled product only proves selected-point mapping"
        )
        raise BoundedSchemaError(message)
    entries = _curve_points(bounded)
    indices = [index for index, _series, _point in entries]
    expected = list(range(len(indices)))
    if indices != expected:
        message = (
            "chunked manifest does not cover the full 0..N-1 sequence "
            f"(coverage mismatch: got {len(indices)} points, indices {indices[:5]}...)"
        )
        raise BoundedSchemaError(message)
    reference = _curve_series_of(bounded.blocks[0].product)
    return CurveSeries(
        ref=reference.ref,
        points=tuple(point for _index, _series, point in entries),
        source=reference.source,
        parameters=reference.parameters,
    )


def selected_point_mapping(
    bounded: BoundedProduct,
) -> tuple[tuple[int, PointRecord], ...]:
    """Return ``(native_index, point)`` pairs of a subsampled manifest, sorted.

    Proves every selected point maps back to a unique original index; it makes
    **no** claim of full-sequence reconstruction.
    """
    entries = _curve_points(bounded)
    indices = [index for index, _series, _point in entries]
    if len(set(indices)) != len(indices):
        message = "selected points must map to unique original indices"
        raise BoundedSchemaError(message)
    if indices != sorted(indices):
        message = "selected points must be sorted by original native index"
        raise BoundedSchemaError(message)
    return tuple((index, point) for index, _series, point in entries)


def reconstruct_structure_frames(bounded: BoundedProduct) -> tuple[FrameRecord, ...]:
    """Losslessly rebuild the full original frame sequence from a chunked manifest."""
    if bounded.display_subsampled:
        message = "reconstruct_structure_frames requires a lossless (chunked) manifest"
        raise BoundedSchemaError(message)
    frames: list[FrameRecord] = []
    seen: set[int] = set()
    for block in bounded.blocks:
        payload = _trajectory_payload_of(block.product)
        for frame in payload.frames:
            if frame.frame_index in seen:
                message = f"duplicate frame_index {frame.frame_index} across blocks"
                raise BoundedSchemaError(message)
            seen.add(frame.frame_index)
            frames.append(frame)
    frames.sort(key=lambda frame: frame.frame_index)
    indices = [frame.frame_index for frame in frames]
    if indices != list(range(len(indices))):
        message = "chunked manifest does not cover the full 0..N-1 frame sequence"
        raise BoundedSchemaError(message)
    return tuple(frames)
