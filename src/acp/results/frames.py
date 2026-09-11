"""Stable frame and view contracts for energy and trajectory projections."""

# ``Any`` is part of the established JSON metadata contract.
# pyright: reportAny=false, reportExplicitAny=false
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ANNOTATION_TYPES",
    "TrajectoryAnnotation",
    "TrajectoryFrame",
    "VIEW_REGISTRY",
    "ViewSpec",
    "view_spec",
]


def _number(value: Any) -> float | None:
    """Return a finite float using the energy-graph number semantics."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: int | None) -> int | None:
    """Return an integer unless the runtime value is non-finite."""
    if value is None or isinstance(value, bool):
        return None
    return value if math.isfinite(value) else None


@dataclass(frozen=True, slots=True)
class TrajectoryFrame:
    """A plot-ready trajectory frame.

    The metadata mapping is copied while emitting; callers must treat it as
    read-only after construction even though the frozen dataclass cannot make
    the mapping itself immutable.
    """

    frame_id: str
    label: str
    frame_index: int
    x: float | None
    energy: float | None
    status: str = "unknown"
    geometry_ref: str = ""
    step: int | None = None
    time_ps: float | None = None
    coordinate: float | None = None
    rms_gradient: float | None = None
    max_gradient: float | None = None
    basin_id: int | None = None
    annotations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_node(self, node_type: str) -> dict[str, Any]:
        """Emit the stable node wire shape used by the energy viewer."""
        metadata = dict(self.metadata)
        scalar_metadata: dict[str, Any] = {
            "step": _integer(self.step),
            "time_ps": _number(self.time_ps),
            "coordinate": _number(self.coordinate),
            "rms_gradient": _number(self.rms_gradient),
            "max_gradient": _number(self.max_gradient),
            "basin_id": _integer(self.basin_id),
            "annotations": list(self.annotations) if self.annotations else None,
        }
        metadata.update({key: value for key, value in scalar_metadata.items() if value is not None})
        return {
            "id": self.frame_id,
            "label": self.label,
            "type": node_type,
            "frame_index": self.frame_index,
            "x": _number(self.x),
            "energy": _number(self.energy),
            "status": self.status,
            "geometry_ref": self.geometry_ref,
            "metadata": metadata,
        }


_ANNOTATION_METADATA_KEYS = frozenset(
    {
        "active",
        "saved",
        "candidate_id",
        "recommended_type",
        "selection_source",
        "confidence",
        "reason",
    }
)


@dataclass(frozen=True, slots=True)
class TrajectoryAnnotation:
    """A marker attached to one trajectory frame."""

    id: str
    type: str
    label: str
    frame_index: int
    x: float | None
    y: float | None
    status: str = ""
    geometry_ref: str = ""
    selected: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_annotation(self) -> dict[str, Any]:
        """Emit the stable annotation wire shape and known extensions."""
        result: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "frame_index": self.frame_index,
            "x": _number(self.x),
            "y": _number(self.y),
            "status": self.status,
            "geometry_ref": self.geometry_ref,
            "selected": self.selected,
        }
        nested_metadata: dict[str, Any] = {}
        for key, value in self.metadata.items():
            if key in _ANNOTATION_METADATA_KEYS:
                result[key] = value
            else:
                nested_metadata[key] = value
        if nested_metadata:
            result["metadata"] = nested_metadata
        return result


ANNOTATION_TYPES = frozenset(
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


@dataclass(frozen=True, slots=True)
class ViewSpec:
    """Default labels and node type for one energy-graph view."""

    view_type: str
    title_zh: str
    title_en: str
    x_label_zh: str
    x_unit: str
    node_type: str


VIEW_REGISTRY: dict[str, ViewSpec] = {
    "scan": ViewSpec(
        "scan", "PES 扫描能量", "PES Scan Energy", "扫描坐标", "angstrom-or-degree", "frame"
    ),
    "scan_trajectory": ViewSpec(
        "scan_trajectory",
        "扫描能量剖面",
        "Scan Energy Profile",
        "扫描坐标",
        "angstrom-or-degree",
        "frame",
    ),
    "optimization": ViewSpec(
        "optimization",
        "几何优化轨迹",
        "Optimization Trajectory",
        "优化周期",
        "cycle",
        "optimization_cycle",
    ),
    "conformer": ViewSpec(
        "conformer",
        "构象能量分布",
        "Conformer Energy Distribution",
        "构象排名",
        "rank",
        "conformer",
    ),
    "sampling": ViewSpec(
        "sampling",
        "构象搜索轨迹",
        "Conformer Search Trajectory",
        "模拟时间",
        "ps",
        "frame",
    ),
    "reaction_path": ViewSpec(
        "reaction_path",
        "反应路径能量图",
        "Reaction Path Energy",
        "反应进程",
        "progress",
        "reaction_point",
    ),
    "irc": ViewSpec("irc", "IRC 能量剖面", "IRC Energy Profile", "反应坐标", "frame", "irc_point"),
    "neb": ViewSpec("neb", "NEB 最小能量路径", "NEB Minimum Energy Path", "路径坐标", "", ""),
    "unsupported": ViewSpec("unsupported", "能量图不可用", "Energy Graph Unavailable", "", "", ""),
}


def view_spec(view_type: str) -> ViewSpec:
    """Return a registered view specification or the unsupported fallback."""
    return VIEW_REGISTRY.get(view_type, VIEW_REGISTRY["unsupported"])
