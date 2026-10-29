"""Science-product → frames/annotation adapter (plan todo 28, B28b).

Projects a versioned :class:`~cccp.calculation.products.contracts.ScienceProduct`
onto the platform frame contract.  Every emitted node goes through
:meth:`acp.results.frames.TrajectoryFrame.to_node` and every emitted marker
through :meth:`acp.results.frames.TrajectoryAnnotation.to_annotation` — this
module never hand-writes the wire shape (root ANTI #21) and never re-implements
a scientific algorithm or parser (C owns those; ``product_from_dict`` is the
single reader).

The mapping is the frozen lane-C handoff ``HN-02``
(``tests/baseline/cccp-evidence/handoffs/HN-02-frames-mapping.md``): field →
node/annotation, unit/identity rules, and the five-state fixture set under
``tests/fixtures/science_products/frames_mapping/``.  ``vibration`` and
``derived`` are explicitly **N/A** to node/annotation (the frame contract does
not carry modes or provenance); their consumption paths are registered in
:data:`PRODUCT_ADAPTER_RECORDS` and must not smuggle extra fields into
``to_node``.

Old-version products are handled by the C reader's explicit conversion path
(``migrate_v0_to_v1``) — the adapter surfaces the conversion decisions in
``migrations`` rather than re-deriving them.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from acp.results.frames import TrajectoryAnnotation, TrajectoryFrame, view_spec
from cccp.calculation.products import (
    CurveScatterPayload,
    ProductKind,
    ProductSchemaError,
    ProductState,
    ScienceProduct,
    StructureTrajectoryPayload,
    product_from_dict,
)

logger = logging.getLogger(__name__)

__all__ = [
    "FramesAdapterError",
    "PRODUCT_ADAPTER_RECORDS",
    "adapter_record_for",
    "project_science_product",
    "project_science_product_dict",
]


class FramesAdapterError(RuntimeError):
    """A product kind has no node/annotation projection (explicit N/A)."""


# ── per-product implementation-closure record (HN-02 §6) ────────────────────
#
# One row per supported product kind: real generation entry (C), C reader/writer,
# B Data registration/read, B frames/annotation adapter, A consume+selection
# mapping, and the N/A rationale.  The registry is data, not an algorithm — it
# records *where* each piece lives so the closure is auditable.

_READER_WRITER = "cccp.calculation.products.serialize.product_to_dict/product_from_dict"
_DATA_PUBLISH = "acp.storage.manifest (v2 product) + acp.results.manifest (read) [B28a]"


def _adapter_row(
    kind: str,
    generation_entry: str,
    frames_adapter: str,
    view_mapping: str,
    frontend_consumer: str,
    node_annotation: str,
) -> Mapping[str, str]:
    return {
        "product_kind": kind,
        "generation_entry": generation_entry,
        "reader_writer": _READER_WRITER,
        "data_publish": _DATA_PUBLISH,
        "frames_adapter": frames_adapter,
        "view_mapping": view_mapping,
        "frontend_consumer": frontend_consumer,
        "node_annotation": node_annotation,
    }


PRODUCT_ADAPTER_RECORDS: tuple[Mapping[str, str], ...] = (
    _adapter_row(
        ProductKind.STRUCTURE_TRAJECTORY.value,
        "cccp.calculation.products.structure.build_structure_trajectory_product",
        "acp.results.frames_adapter.project_science_product -> to_node()",
        "view_type -> VIEW_REGISTRY.node_type; geometry via structure endpoints",
        "energy viewer node; structure viewer geometry by atom_uid",
        "node (skipped_frames carries missing_reason; never zero-filled)",
    ),
    _adapter_row(
        ProductKind.CURVE_SCATTER.value,
        "cccp.calculation.products.curve.build_curve_scatter_product",
        "acp.results.frames_adapter.project_science_product -> to_node()/to_annotation()",
        "view_type -> VIEW_REGISTRY.node_type; x_unit/y_unit metadata",
        "energy viewer scan/irc/sampling; minimum + candidate annotations",
        "node (x/energy) + minimum annotation (skipped_points carries missing_reason)",
    ),
    _adapter_row(
        ProductKind.VIBRATION.value,
        "cccp.calculation.products.vibration.build_vibration_product",
        "N/A to node/annotation - structure-viewer vibration projection",
        "N/A (frames/annotation contract carries no modes/displacements)",
        "structure viewer GET .../vibrations (mode_index/frequency_units/atom_order)",
        "N/A (explicit)",
    ),
    _adapter_row(
        ProductKind.DERIVED.value,
        "cccp.calculation.products.derived.build_derived_product",
        "N/A to node/annotation - provenance/selection metadata only",
        "N/A (provenance envelope; feeds candidate selection metadata)",
        "candidate selection / provenance display (recommended_type/confidence/reason)",
        "N/A (explicit)",
    ),
)


def adapter_record_for(product_kind: str) -> Mapping[str, str] | None:
    """Return the implementation-closure record for *product_kind*, or ``None``."""
    for record in PRODUCT_ADAPTER_RECORDS:
        if record["product_kind"] == product_kind:
            return record
    return None


# ── projection ──────────────────────────────────────────────────────────────


def _base(view_type: str) -> dict[str, Any]:
    spec = view_spec(view_type)
    return {"view_type": spec.view_type, "node_type": spec.node_type}


def _structure_projection(
    product: ScienceProduct, payload: StructureTrajectoryPayload, view_type: str
) -> dict[str, Any]:
    node_type = view_spec(view_type).node_type
    nodes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for frame in payload.frames:
        if frame.missing_reason is not None:
            # Missing science data is never zero-filled; the frame keeps its
            # native index and is reported with an explicit reason.
            skipped.append(
                {"frame_index": frame.frame_index, "missing_reason": frame.missing_reason.value}
            )
            continue
        metadata: dict[str, Any] = {
            "topology_source": frame.topology_source,
            "coordinate_units": frame.coordinate_units.value,
        }
        if frame.block_index is not None:
            metadata["block_index"] = frame.block_index
        if frame.source is not None:
            metadata["source"] = frame.source.to_dict()
        trajectory_frame = TrajectoryFrame(
            frame_id=str(frame.frame_id),
            label=frame.label or f"Frame {frame.frame_index + 1}",
            frame_index=frame.frame_index,
            x=None,
            energy=None,
            geometry_ref=frame.source.artifact if frame.source is not None else "",
            metadata=metadata,
        )
        nodes.append(trajectory_frame.to_node(node_type))
    return {
        **_base(view_type),
        "nodes": nodes,
        "annotations": [],
        "skipped_frames": skipped,
        "unavailable": None,
        "migrations": list(product.migrations),
    }


def _minimum_annotation(nodes: list[dict[str, Any]], sequence: int) -> dict[str, Any]:
    finite = [
        node
        for node in nodes
        if isinstance(node.get("energy"), (int, float)) and not isinstance(node.get("energy"), bool)
    ]
    if not finite:
        return {}
    lowest = min(finite, key=lambda node: float(node["energy"]))
    annotation = TrajectoryAnnotation(
        id=f"minimum_{sequence}",
        type="minimum",
        label="最低能量",
        frame_index=int(lowest["frame_index"]),
        x=lowest["x"],
        y=lowest["energy"],
        status=str(lowest["status"]),
        geometry_ref=str(lowest["geometry_ref"]),
        selected=False,
    )
    return annotation.to_annotation()


def _curve_projection(
    product: ScienceProduct, payload: CurveScatterPayload, view_type: str
) -> dict[str, Any]:
    node_type = view_spec(view_type).node_type
    nodes: list[dict[str, Any]] = []
    skipped_points: list[dict[str, Any]] = []
    skipped_series: list[dict[str, Any]] = []
    for series in payload.series:
        if series.missing_reason is not None:
            # Whole series unavailable: never emit fabricated points.
            skipped_series.append(
                {
                    "series_id": series.ref.series_id,
                    "missing_reason": series.missing_reason.value,
                }
            )
            continue
        metadata = {
            "series_id": series.ref.series_id,
            "x_unit": series.ref.x_unit,
            "y_unit": series.ref.y_unit,
        }
        for index, point in enumerate(series.points):
            if point.missing_reason is not None:
                skipped_points.append(
                    {"point_id": point.point_id, "missing_reason": point.missing_reason.value}
                )
                continue
            native = (
                point.source.native_index
                if point.source is not None and point.source.native_index is not None
                else index
            )
            trajectory_frame = TrajectoryFrame(
                frame_id=point.point_id,
                label=f"Point {index}",
                frame_index=int(native),
                x=point.x,
                energy=point.y,
                status="completed",
                geometry_ref=point.source.artifact if point.source is not None else "",
                metadata=metadata,
            )
            nodes.append(trajectory_frame.to_node(node_type))
    annotations: list[dict[str, Any]] = []
    minimum = _minimum_annotation(nodes, 0)
    if minimum:
        annotations.append(minimum)
    projection: dict[str, Any] = {
        **_base(view_type),
        "nodes": nodes,
        "annotations": annotations,
        "skipped_points": skipped_points,
    }
    if skipped_series:
        projection["skipped_series"] = skipped_series
    projection["unavailable"] = None
    projection["migrations"] = list(product.migrations)
    return projection


def project_science_product(product: ScienceProduct, *, view_type: str) -> dict[str, Any]:
    """Project a parsed *product* onto the frame contract for *view_type*.

    Raises:
        FramesAdapterError: For a product kind with an explicit N/A mapping
            (``vibration`` / ``derived``) or an available product without a
            recognised payload.  Non-available envelopes are projected to the
            typed ``unavailable`` shape instead of raising.
    """
    if product.state is not ProductState.AVAILABLE:
        reason = product.missing_reason.value if product.missing_reason is not None else None
        return {
            **_base(view_type),
            "nodes": [],
            "annotations": [],
            "skipped_frames": [],
            "unavailable": {"state": product.state.value, "missing_reason": reason},
            "migrations": list(product.migrations),
        }
    payload = product.payload
    if isinstance(payload, StructureTrajectoryPayload):
        return _structure_projection(product, payload, view_type)
    if isinstance(payload, CurveScatterPayload):
        return _curve_projection(product, payload, view_type)
    kind = product.identity.product_kind
    raise FramesAdapterError(
        f"product kind {kind.value!r} has no node/annotation projection "
        "(explicit N/A; see PRODUCT_ADAPTER_RECORDS)"
    )


def project_science_product_dict(payload: Mapping[str, Any], *, view_type: str) -> dict[str, Any]:
    """Read *payload* (the C reader) and project it; corrupt input is typed.

    A structurally corrupt payload yields an explicit ``ProductSchemaError``
    projection (never a partial render); older versions go through the reader's
    explicit migration path and surface their decisions in ``migrations``.
    """
    try:
        product = product_from_dict(dict(payload))
    except ProductSchemaError as exc:
        logger.warning("frames adapter: corrupt science product (%s)", exc)
        return {
            **_base(view_type),
            "error": "ProductSchemaError",
            "stage": "product_from_dict",
            "message": str(exc),
            "nodes": [],
            "annotations": [],
        }
    return project_science_product(product, view_type=view_type)
