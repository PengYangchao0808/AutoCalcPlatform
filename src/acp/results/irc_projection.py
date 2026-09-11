"""IRC frame projection for forward/reverse intrinsic reaction paths.

Builds the frames-contract energy-graph projection for the ``irc`` view from
``RESULT/irc/irc_forward.xyz`` and ``RESULT/irc/irc_reverse.xyz``.  Each
direction becomes its own series; nodes keep STRICT FILE ORDER (path order is
physically meaningful for an IRC and is never reordered by energy).  Frame
comments carry the geometry reference and — when the writer emits them — an
energy value; the current IRC primitive writes single-frame endpoint files
with the title ``IRC {direction} endpoint`` (no energy), in which case the
projection degrades to path-order nodes without a series.

This module is display-only: it shows path order and endpoints, and does not
perform connectivity analysis or TS-identity validation (that belongs to
``acp.calculations.irc.validation``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acp.results.frames import VIEW_REGISTRY, TrajectoryAnnotation, TrajectoryFrame

logger = logging.getLogger(__name__)

__all__ = [
    "IRC_DIRECTIONS",
    "IrcFrameBlock",
    "build_irc_energy_graph",
    "frame_energy_from_comment",
    "parse_irc_xyz_frames",
]

IRC_DIRECTIONS: tuple[str, ...] = ("forward", "reverse")

_DIRECTION_LABELS_ZH = {"forward": "正向", "reverse": "反向"}

# IRC geometry files live under RESULT/irc/ (irc primitive writer contract).
_GEOMETRY_REF_TEMPLATE = "RESULT/irc/irc_{direction}.xyz"

# "E = -76.1234" / "energy: -76.1234" / "Energy = ..." anchored match first;
# a bare decimal float ("IRC 2.5000 endpoint" style) as fallback.
_ENERGY_ANCHORED_RE = re.compile(
    r"(?:^|\s)(?:e|energy)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE
)
_DECIMAL_RE = re.compile(r"-?\d+\.\d+")


@dataclass(frozen=True, slots=True)
class IrcFrameBlock:
    """One frame block inside a (possibly multi-frame) IRC XYZ file."""

    index: int
    comment: str
    atom_count: int


def parse_irc_xyz_frames(path: Path) -> list[IrcFrameBlock]:
    """Parse a single- or multi-frame XYZ file into frame blocks.

    Mirrors the block-walk semantics of
    ``acp.confsearch.sampling_models.read_traj_frame_xyz`` (atom-count header,
    comment line, N atom rows) so per-frame geometry extraction and frame
    counting agree on block boundaries.  Returns ``[]`` on unreadable files.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.debug("Cannot read IRC trajectory %s", path, exc_info=True)
        return []
    lines = text.splitlines()
    blocks: list[IrcFrameBlock] = []
    offset = 0
    index = 0
    while offset < len(lines):
        header = lines[offset].strip()
        if not header:
            offset += 1
            continue
        try:
            atom_count = int(header)
        except ValueError:
            offset += 1
            continue
        if atom_count <= 0:
            break
        end = offset + 2 + atom_count
        if end > len(lines):
            break
        blocks.append(
            IrcFrameBlock(
                index=index,
                comment=lines[offset + 1].strip(),
                atom_count=atom_count,
            )
        )
        index += 1
        offset = end
    return blocks


def frame_energy_from_comment(comment: str) -> float | None:
    """Extract an energy from a frame comment line, else ``None``.

    Accepts ``E = -76.12`` / ``energy: -76.12`` anchors first; otherwise a
    lone decimal float anywhere in the comment.  Writer titles like
    ``IRC forward endpoint`` (no number) yield ``None``.
    """
    if not comment:
        return None
    anchored = _ENERGY_ANCHORED_RE.search(comment)
    if anchored:
        try:
            return float(anchored.group(1))
        except ValueError:
            return None
    decimals = _DECIMAL_RE.findall(comment)
    if len(decimals) == 1:
        try:
            return float(decimals[0])
        except ValueError:
            return None
    return None


def build_irc_energy_graph(job_id: str, work_dir: Path) -> dict[str, Any] | None:
    """Build the ``irc`` frames-contract projection for a job.

    Returns ``None`` when neither direction file exists.  Path order equals
    file order — nodes are NEVER reordered by energy.  A missing direction
    degrades to a single series with a note in ``metadata.warnings``.
    """
    irc_dir = Path(work_dir) / "RESULT" / "irc"
    per_direction: dict[str, list[IrcFrameBlock]] = {}
    notes: list[str] = []
    for direction in IRC_DIRECTIONS:
        path = irc_dir / f"irc_{direction}.xyz"
        if not path.is_file():
            notes.append(f"missing_direction:{direction}")
            continue
        blocks = parse_irc_xyz_frames(path)
        if not blocks:
            notes.append(f"unparseable_direction:{direction}")
            continue
        per_direction[direction] = blocks
    if not per_direction:
        return None
    return _build_graph(job_id, per_direction, notes)


def _build_graph(
    job_id: str,
    per_direction: dict[str, list[IrcFrameBlock]],
    notes: list[str],
) -> dict[str, Any]:
    spec = VIEW_REGISTRY["irc"]
    raw_energies: dict[str, list[float | None]] = {
        direction: [frame_energy_from_comment(b.comment) for b in blocks]
        for direction, blocks in per_direction.items()
    }
    finite = [e for values in raw_energies.values() for e in values if e is not None]
    energy_available = bool(finite)
    min_energy = min(finite) if finite else None

    nodes: list[dict[str, Any]] = []
    node_slots: dict[str, list[int]] = {}
    for direction in IRC_DIRECTIONS:
        blocks = per_direction.get(direction)
        if not blocks:
            continue
        label = _DIRECTION_LABELS_ZH[direction]
        slots: list[int] = []
        for block in blocks:
            raw = raw_energies[direction][block.index]
            relative = None if raw is None or min_energy is None else raw - min_energy
            slots.append(len(nodes))
            nodes.append(
                TrajectoryFrame(
                    frame_id=f"irc_{direction}_{block.index}",
                    label=f"IRC {label} {block.index + 1}",
                    frame_index=block.index,
                    x=float(block.index),
                    energy=relative,
                    status="completed" if raw is not None else "unknown",
                    geometry_ref=_GEOMETRY_REF_TEMPLATE.format(direction=direction),
                    step=block.index,
                    metadata={
                        "direction": direction,
                        "energy_raw": raw,
                    },
                ).to_node(spec.node_type)
            )
        node_slots[direction] = slots

    series: list[dict[str, Any]] = []
    for direction in IRC_DIRECTIONS:
        blocks = per_direction.get(direction)
        if not blocks:
            continue
        values: list[float | None] = [None] * len(nodes)
        for slot, block in zip(node_slots[direction], blocks):
            raw = raw_energies[direction][block.index]
            values[slot] = None if raw is None or min_energy is None else raw - min_energy
        if not any(v is not None for v in values):
            continue
        series.append(
            {
                "id": f"irc_{direction}",
                "label": _DIRECTION_LABELS_ZH[direction],
                "unit": "kcal/mol",
                "axis": "left",
                "values": values,
            }
        )

    annotations: list[dict[str, Any]] = []
    if energy_available:
        for direction in IRC_DIRECTIONS:
            slots = node_slots.get(direction)
            if not slots:
                continue
            values = [raw_energies[direction][b.index] for b in per_direction[direction]]
            finite_dir = [v for v in values if v is not None]
            if not finite_dir:
                continue
            # Endpoint marker only when the path endpoint is also the
            # directional minimum (the usual IRC descent) — display-only.
            if values[-1] is not None and values[-1] == min(finite_dir):
                node = nodes[slots[-1]]
                annotations.append(
                    TrajectoryAnnotation(
                        id=f"irc_{direction}_endpoint",
                        type="minimum",
                        label=f"{_DIRECTION_LABELS_ZH[direction]}终点",
                        frame_index=node["frame_index"],
                        x=node["x"],
                        y=node["energy"],
                        status=node["status"],
                        geometry_ref=node["geometry_ref"],
                    ).to_annotation()
                )

    default_series = "irc_forward" if any(s["id"] == "irc_forward" for s in series) else (
        series[0]["id"] if series else ""
    )

    return {
        "job_id": job_id,
        "view_type": "irc",
        "title": spec.title_zh,
        "status": "completed",
        "complete": True,
        "revision": "",
        "default_series": default_series,
        "available_views": ["irc"],
        "x_axis": {"label": spec.x_label_zh, "unit": spec.x_unit},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": "RESULT/irc/",
        "provenance": {},
        "metadata": {
            "frame_count": len(nodes),
            "directions": {d: len(per_direction[d]) for d in per_direction},
            "energy_available": energy_available,
            "warnings": notes,
        },
    }
