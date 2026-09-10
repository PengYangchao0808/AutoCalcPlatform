from __future__ import annotations

import math

import pytest

from acp.results.frames import (
    ANNOTATION_TYPES,
    VIEW_REGISTRY,
    TrajectoryAnnotation,
    TrajectoryFrame,
    ViewSpec,
    view_spec,
)

NODE_KEYS = {
    "id",
    "label",
    "type",
    "frame_index",
    "x",
    "energy",
    "status",
    "geometry_ref",
    "metadata",
}
ANNOTATION_KEYS = {
    "id",
    "type",
    "label",
    "frame_index",
    "x",
    "y",
    "status",
    "geometry_ref",
    "selected",
}


@pytest.mark.parametrize(
    ("frame", "node_type"),
    (
        (
            TrajectoryFrame(
                frame_id="frame_0",
                label="Frame 1",
                frame_index=0,
                x=1.25,
                energy=0.0,
                geometry_ref="RESULT/scan/frame_000.xyz",
            ),
            "frame",
        ),
        (
            TrajectoryFrame(
                frame_id="cycle_0",
                label="Cycle 1",
                frame_index=0,
                x=1.0,
                energy=0.0,
                status="converged",
                geometry_ref="RESULT/optimization/cycle_0001.xyz",
            ),
            "optimization_cycle",
        ),
        (
            TrajectoryFrame(
                frame_id="route_0_point_0",
                label="Point 1",
                frame_index=0,
                x=0.0,
                energy=0.0,
                status="completed",
            ),
            "reaction_point",
        ),
        (
            TrajectoryFrame(
                frame_id="conf_1",
                label="conf_1",
                frame_index=0,
                x=1.0,
                energy=0.0,
                status="completed",
                geometry_ref="RESULT/confsearch/conf_1.xyz",
            ),
            "conformer",
        ),
    ),
)
def test_to_node_preserves_documented_wire_key_set(frame: TrajectoryFrame, node_type: str) -> None:
    # Given: one of the four existing energy-graph node shapes.
    # When: the frame is emitted through the common contract.
    node = frame.to_node(node_type)

    # Then: the frontend-facing key set remains exactly unchanged.
    assert set(node) == NODE_KEYS
    assert node["type"] == node_type


def test_to_node_merges_metadata_and_rejects_non_finite_scalars() -> None:
    # Given: caller metadata and finite/non-finite frame values.
    caller_metadata = {"source": "fixture", "custom": {"keep": True}}
    frame = TrajectoryFrame(
        frame_id="sample_3",
        label="Sample 4",
        frame_index=3,
        x=math.nan,
        energy=math.inf,
        step=0,
        time_ps=-math.inf,
        coordinate=2.5,
        rms_gradient=math.nan,
        max_gradient=0.0,
        basin_id=0,
        annotations=("new_basin",),
        metadata=caller_metadata,
    )

    # When: the node is emitted.
    node = frame.to_node("frame")

    # Then: non-finite values become null/are omitted, while metadata is additive.
    assert node["x"] is None
    assert node["energy"] is None
    assert node["metadata"] == {
        "source": "fixture",
        "custom": {"keep": True},
        "step": 0,
        "coordinate": 2.5,
        "max_gradient": 0.0,
        "basin_id": 0,
        "annotations": ["new_basin"],
    }
    assert caller_metadata == {"source": "fixture", "custom": {"keep": True}}


@pytest.mark.parametrize(
    ("annotation", "expected_keys"),
    (
        (
            TrajectoryAnnotation(
                id="minimum_0",
                type="minimum",
                label="最低能量",
                frame_index=0,
                x=1.0,
                y=0.0,
            ),
            ANNOTATION_KEYS,
        ),
        (
            TrajectoryAnnotation(
                id="candidate_0",
                type="ts",
                label="TS 1",
                frame_index=0,
                x=1.0,
                y=12.5,
                status="candidate",
                geometry_ref="RESULT/scan/frame_000.xyz",
                selected=True,
                metadata={
                    "candidate_id": "candidate_0",
                    "active": True,
                    "saved": False,
                    "recommended_type": "ts",
                    "selection_source": "algorithm",
                    "confidence": 0.9,
                    "reason": "barrier",
                    "internal_note": "nested",
                },
            ),
            ANNOTATION_KEYS
            | {
                "candidate_id",
                "active",
                "saved",
                "recommended_type",
                "selection_source",
                "confidence",
                "reason",
                "metadata",
            },
        ),
    ),
)
def test_to_annotation_preserves_documented_wire_key_sets(
    annotation: TrajectoryAnnotation, expected_keys: set[str]
) -> None:
    # Given: one of the current minimum or candidate annotation shapes.
    # When: the annotation is emitted through the common contract.
    result = annotation.to_annotation()

    # Then: only established top-level extension keys are added.
    assert set(result) == expected_keys
    if annotation.type == "ts":
        assert result["candidate_id"] == "candidate_0"
        assert result["metadata"] == {"internal_note": "nested"}


def test_annotation_types_are_closed() -> None:
    assert ANNOTATION_TYPES == frozenset(
        {
            "ts",
            "intermediate",
            "minimum",
            "failed",
            "new_basin",
            "cluster_representative",
            "locked",
            "user_selected",
        }
    )


def test_view_registry_is_complete_and_localized() -> None:
    # Given: the documented registry entries.
    expected_view_types = {
        "scan",
        "scan_trajectory",
        "optimization",
        "conformer",
        "sampling",
        "reaction_path",
        "irc",
        "neb",
        "unsupported",
    }

    # Then: the registry is closed over exactly those view types.
    assert set(VIEW_REGISTRY) == expected_view_types
    assert VIEW_REGISTRY["scan"] == ViewSpec(
        "scan", "PES 扫描能量", "PES Scan Energy", "扫描坐标", "angstrom-or-degree", "frame"
    )
    assert VIEW_REGISTRY["optimization"].title_zh == "几何优化轨迹"
    assert VIEW_REGISTRY["optimization"].title_en == "Optimization Trajectory"
    assert VIEW_REGISTRY["sampling"].x_unit == "ps"


def test_view_spec_falls_back_to_unsupported() -> None:
    # Given: an unknown view identifier.
    # When: the registry is queried.
    result = view_spec("bogus")

    # Then: no exception escapes and the explicit fallback is returned.
    assert result is VIEW_REGISTRY["unsupported"]
