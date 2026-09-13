"""IRC frame projection for forward/reverse intrinsic reaction paths.

Builds the frames-contract energy-graph projection for the ``irc`` view.  The
preferred source is the ``irc_trajectory_v1`` snapshot produced by the IRC
primitive (``RESULT/trajectories/irc_trajectory.json``); historical jobs that
only left ORCA output under ``WORK/<stage>/ORCA`` are back-filled by parsing
the ``*_IRC_[FB]_trj.xyz`` trajectories directly.  ``RESULT/irc/irc_*.xyz``
endpoint files are a compatibility fallback and never fabricate a
path curve from endpoint geometry alone.

Each direction becomes its own series; nodes keep STRICT FILE ORDER (path
order is physically meaningful for an IRC and is never reordered by energy).
This module is display-only: it does not perform connectivity analysis or
TS-identity validation (that belongs to ``acp.calculations.irc.validation``).
"""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownParameterType=false
from __future__ import annotations

import json
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
    "build_irc_pending_energy_graph",
    "frame_energy_from_comment",
    "parse_irc_xyz_frames",
]

IRC_DIRECTIONS: tuple[str, ...] = ("forward", "reverse")

_DIRECTION_LABELS_ZH = {"forward": "正向", "reverse": "反向"}

# IRC geometry files live under RESULT/irc/ (irc primitive writer contract).
_GEOMETRY_REF_TEMPLATE = "RESULT/irc/irc_{direction}.xyz"

# "E = -76.1234" / "energy: -76.1234" / "Energy = ..." anchored match first;
# a bare decimal float ("IRC 2.5000 endpoint" style) as fallback.
_ENERGY_ANCHORED_RE = re.compile(r"(?:^|\s)(?:e|energy)\s*[=:]\s*(-?\d+(?:\.\d+)?)", re.IGNORECASE)
_DECIMAL_RE = re.compile(r"-?\d+\.\d+")


@dataclass(frozen=True, slots=True)
class IrcFrameBlock:
    """One frame block inside a (possibly multi-frame) IRC XYZ file."""

    index: int
    comment: str
    atom_count: int


@dataclass(frozen=True, slots=True)
class _PathFrame:
    """One normalised IRC path point (trajectory JSON or endpoint XYZ)."""

    index: int
    comment: str
    energy_raw: float | None
    geometry_ref: str
    status: str


@dataclass(frozen=True, slots=True)
class _PathInput:
    """Normalised path data plus projection status for one source."""

    per_direction: dict[str, list[_PathFrame]]
    status: str
    complete: bool
    source: str
    live: bool
    merged: bool = False
    ts_energy: float | None = None
    ts_ref: str = ""


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


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _manifest_status(work_dir: Path) -> str:
    payload = _read_json(work_dir / "RESULT" / "result_manifest.json")
    return str(payload.get("status") or "") if payload else ""


def _resolve_ts_from_work(
    work_dir: Path, per_direction: dict[str, list[_PathFrame]]
) -> float | None:
    """TS reference energy from ORCA output left under ``WORK`` (when available)."""
    from cccp.qc.interfaces.orca_ts import resolve_irc_ts_energy

    candidates = [work_dir / "WORK" / "07_PATH" / "ORCA"]
    candidates.extend(path for path in work_dir.glob("WORK/*/ORCA") if path.is_dir())
    reverse_frames = len(per_direction.get("reverse") or [])
    for orca_dir in candidates:
        if not orca_dir.is_dir():
            continue
        ts_energy = resolve_irc_ts_energy(orca_dir, reverse_frames=reverse_frames)
        if ts_energy is not None:
            return ts_energy
    return None


def _trajectory_input(work_dir: Path) -> _PathInput | None:
    """Build a projection input from the persisted ``irc_trajectory_v1`` file."""
    payload = _read_json(work_dir / "RESULT" / "trajectories" / "irc_trajectory.json")
    if payload is None:
        return None
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list):
        return None
    per_direction: dict[str, list[_PathFrame]] = {}
    for entry in raw_frames:
        if not isinstance(entry, dict):
            continue
        direction = str(entry.get("direction") or "")
        if direction not in IRC_DIRECTIONS:
            continue
        index = entry.get("index", entry.get("frame_index"))
        try:
            frame_index = int(str(index))
        except (TypeError, ValueError):
            continue
        per_direction.setdefault(direction, []).append(
            _PathFrame(
                index=frame_index,
                comment=str(entry.get("comment") or ""),
                energy_raw=_coerce_float(entry.get("energy_hartree")),
                geometry_ref=str(entry.get("geometry_ref") or ""),
                status=str(entry.get("status") or "completed"),
            )
        )
    if not any(per_direction.values()):
        return None
    status = str(payload.get("status") or "completed")
    complete = bool(payload.get("complete"))
    ts_energy = _coerce_float(payload.get("ts_energy_hartree"))
    if ts_energy is None:
        ts_energy = _resolve_ts_from_work(work_dir, per_direction)
    return _PathInput(
        per_direction=per_direction,
        status=status,
        complete=complete,
        source="RESULT/trajectories/irc_trajectory.json",
        live=not complete,
        merged=True,
        ts_energy=ts_energy,
        ts_ref="input.xyz" if (work_dir / "input.xyz").is_file() else "",
    )


def _work_trajectory_input(work_dir: Path) -> _PathInput | None:
    """Back-fill a path from historical ORCA trajectories left under ``WORK``."""
    from cccp.qc.interfaces.orca_ts import (
        discover_irc_trajectory_files,
        parse_irc_trajectory_xyz,
    )

    candidates = [work_dir / "WORK" / "07_PATH" / "ORCA"]
    candidates.extend(path for path in work_dir.glob("WORK/*/ORCA") if path.is_dir())
    for orca_dir in candidates:
        if not orca_dir.is_dir():
            continue
        files = discover_irc_trajectory_files(orca_dir)
        if not files:
            continue
        per_direction: dict[str, list[_PathFrame]] = {}
        for direction in IRC_DIRECTIONS:
            trajectory_path = files.get(direction)
            if trajectory_path is None:
                continue
            points = parse_irc_trajectory_xyz(trajectory_path, direction)
            if not points:
                continue
            try:
                geometry_ref = trajectory_path.relative_to(work_dir).as_posix()
            except ValueError:
                geometry_ref = str(trajectory_path)
            per_direction[direction] = [
                _PathFrame(
                    index=point.index,
                    comment=point.comment,
                    energy_raw=point.energy_hartree,
                    geometry_ref=geometry_ref,
                    status="completed",
                )
                for point in points
            ]
        if not per_direction:
            continue
        manifest_status = _manifest_status(work_dir)
        terminal = manifest_status in {"completed", "failed", "cancelled"}
        from cccp.qc.interfaces.orca_ts import resolve_irc_ts_energy

        reverse_frames = len(per_direction.get("reverse") or [])
        return _PathInput(
            per_direction=per_direction,
            status=manifest_status or "running",
            complete=manifest_status == "completed",
            source=orca_dir.relative_to(work_dir).as_posix(),
            live=not terminal,
            merged=True,
            ts_energy=resolve_irc_ts_energy(orca_dir, reverse_frames=reverse_frames),
            ts_ref="input.xyz" if (work_dir / "input.xyz").is_file() else "",
        )
    return None


def _endpoint_input(work_dir: Path) -> tuple[_PathInput, list[str]] | None:
    """Compatibility fallback: parse ``RESULT/irc/irc_*.xyz`` endpoints."""
    irc_dir = work_dir / "RESULT" / "irc"
    per_direction: dict[str, list[_PathFrame]] = {}
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
        geometry_ref = _GEOMETRY_REF_TEMPLATE.format(direction=direction)
        frames: list[_PathFrame] = []
        for block in blocks:
            energy = frame_energy_from_comment(block.comment)
            frames.append(
                _PathFrame(
                    index=block.index,
                    comment=block.comment,
                    energy_raw=energy,
                    geometry_ref=geometry_ref,
                    status="completed" if energy is not None else "unknown",
                )
            )
        per_direction[direction] = frames
    if not per_direction:
        return None
    return (
        _PathInput(
            per_direction=per_direction,
            status="completed",
            complete=True,
            source="RESULT/irc/",
            live=False,
        ),
        notes,
    )


def build_irc_energy_graph(job_id: str, work_dir: Path) -> dict[str, Any] | None:
    """Build the ``irc`` frames-contract projection for a job.

    Resolution order: persisted trajectory JSON, historical ORCA trajectories
    under ``WORK``, then ``RESULT/irc/irc_*.xyz`` endpoints.  Returns ``None``
    when no source has any point.  Path order equals source order — nodes are
    NEVER reordered by energy.
    """
    work_dir = Path(work_dir)
    path_input = _trajectory_input(work_dir) or _work_trajectory_input(work_dir)
    notes: list[str] = []
    if path_input is None:
        fallback = _endpoint_input(work_dir)
        if fallback is None:
            return None
        path_input, notes = fallback
    return _build_graph(
        job_id,
        path_input.per_direction,
        notes,
        status=path_input.status,
        complete=path_input.complete,
        source=path_input.source,
        live=path_input.live,
        merged=path_input.merged,
        ts_energy=path_input.ts_energy,
        ts_ref=path_input.ts_ref,
    )


def build_irc_pending_energy_graph(job_id: str, work_dir: Path | None = None) -> dict[str, Any]:
    """Projection returned while an IRC path has no usable points yet."""
    spec = VIEW_REGISTRY["irc"]
    manifest_status = _manifest_status(Path(work_dir)) if work_dir is not None else ""
    terminal = manifest_status in {"completed", "failed", "cancelled"}
    return {
        "job_id": job_id,
        "view_type": "irc",
        "title": spec.title_zh,
        "status": manifest_status or "running",
        "complete": False,
        "revision": "",
        "default_series": "",
        "available_views": ["irc"],
        "x_axis": {"label": spec.x_label_zh, "unit": spec.x_unit},
        "series": [],
        "nodes": [],
        "edges": [],
        "annotations": [],
        "source": "RESULT/trajectories/irc_trajectory.json",
        "provenance": {},
        "metadata": {
            "reason": "irc_path_missing" if terminal else "irc_path_pending",
            "workflow": "irc",
            "live": not terminal,
        },
    }


def _build_graph(
    job_id: str,
    per_direction: dict[str, list[_PathFrame]],
    notes: list[str],
    *,
    status: str = "completed",
    complete: bool = True,
    source: str = "RESULT/irc/",
    live: bool = False,
    merged: bool = False,
    ts_energy: float | None = None,
    ts_ref: str = "",
) -> dict[str, Any]:
    if merged:
        return _build_merged_graph(
            job_id,
            per_direction,
            notes,
            status=status,
            complete=complete,
            source=source,
            live=live,
            ts_energy=ts_energy,
            ts_ref=ts_ref,
        )
    spec = VIEW_REGISTRY["irc"]
    raw_energies: dict[str, list[float | None]] = {
        direction: [frame.energy_raw for frame in frames]
        for direction, frames in per_direction.items()
    }
    finite = [e for values in raw_energies.values() for e in values if e is not None]
    energy_available = bool(finite)
    min_energy = min(finite) if finite else None

    nodes: list[dict[str, Any]] = []
    node_slots: dict[str, list[int]] = {}
    for direction in IRC_DIRECTIONS:
        frames = per_direction.get(direction)
        if not frames:
            continue
        label = _DIRECTION_LABELS_ZH[direction]
        slots: list[int] = []
        for frame in frames:
            raw = frame.energy_raw
            relative = None if raw is None or min_energy is None else raw - min_energy
            slots.append(len(nodes))
            nodes.append(
                TrajectoryFrame(
                    frame_id=f"irc_{direction}_{frame.index}",
                    label=f"IRC {label} {frame.index + 1}",
                    frame_index=frame.index,
                    x=float(frame.index),
                    energy=relative,
                    status=frame.status or ("completed" if raw is not None else "unknown"),
                    geometry_ref=frame.geometry_ref,
                    step=frame.index,
                    metadata={
                        "direction": direction,
                        "energy_raw": raw,
                    },
                ).to_node(spec.node_type)
            )
        node_slots[direction] = slots

    series: list[dict[str, Any]] = []
    for direction in IRC_DIRECTIONS:
        frames = per_direction.get(direction)
        if not frames:
            continue
        values: list[float | None] = [None] * len(nodes)
        for slot, frame in zip(node_slots[direction], frames):
            raw = frame.energy_raw
            values[slot] = None if raw is None or min_energy is None else raw - min_energy
        if not any(v is not None for v in values):
            continue
        series.append(
            {
                "id": f"irc_{direction}",
                "label": _DIRECTION_LABELS_ZH[direction],
                "unit": "Eh",
                "axis": "left",
                "values": values,
            }
        )

    annotations: list[dict[str, Any]] = []
    if energy_available:
        for direction in IRC_DIRECTIONS:
            direction_slots = node_slots.get(direction)
            if not direction_slots:
                continue
            values = [frame.energy_raw for frame in per_direction[direction]]
            finite_dir = [v for v in values if v is not None]
            if not finite_dir:
                continue
            # Endpoint marker only when the path endpoint is also the
            # directional minimum (the usual IRC descent) — display-only.
            if values[-1] is not None and values[-1] == min(finite_dir):
                node = nodes[direction_slots[-1]]
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

    default_series = (
        "irc_forward"
        if any(s["id"] == "irc_forward" for s in series)
        else (series[0]["id"] if series else "")
    )

    return {
        "job_id": job_id,
        "view_type": "irc",
        "title": spec.title_zh,
        "status": status or "completed",
        "complete": bool(complete),
        "revision": "",
        "default_series": default_series,
        "available_views": ["irc"],
        "x_axis": {"label": spec.x_label_zh, "unit": spec.x_unit},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": source,
        "provenance": {},
        "metadata": {
            "frame_count": len(nodes),
            "directions": {d: len(per_direction[d]) for d in per_direction},
            "energy_available": energy_available,
            "warnings": notes,
            "live": bool(live),
        },
    }


def _build_merged_graph(
    job_id: str,
    per_direction: dict[str, list[_PathFrame]],
    notes: list[str],
    *,
    status: str,
    complete: bool,
    source: str,
    live: bool,
    ts_energy: float | None,
    ts_ref: str,
) -> dict[str, Any]:
    """Merge both directions around the TS: reverse ← 0 → forward.

    X is a signed step count (TS = 0, reverse negative, forward positive), so
    the real sampling spacing (e.g. 5 vs 50 frames) is preserved instead of
    stretching both sides to equal length.  Y is ``E - E_TS`` for both sides;
    when the TS energy cannot be resolved, the shared global minimum is used
    and a warning is recorded.
    """
    spec = VIEW_REGISTRY["irc"]
    finite = [
        frame.energy_raw
        for frames in per_direction.values()
        for frame in frames
        if frame.energy_raw is not None
    ]
    if ts_energy is None:
        if finite:
            notes.append("ts_energy_missing")
        reference = min(finite) if finite else None
        reference_kind = "min"
    else:
        reference = ts_energy
        reference_kind = "ts"

    nodes: list[dict[str, Any]] = []
    ordered: list[tuple[str, _PathFrame | None]] = []
    for frame in reversed(per_direction.get("reverse") or []):
        ordered.append(("reverse", frame))
    ordered.append(("ts", None))
    for frame in per_direction.get("forward") or []:
        ordered.append(("forward", frame))

    slots: dict[tuple[str, int], int] = {}
    for direction, frame in ordered:
        if direction == "ts":
            energy = 0.0 if reference_kind == "ts" else None
            nodes.append(
                TrajectoryFrame(
                    frame_id="irc_ts",
                    label="TS",
                    frame_index=-1,
                    x=0.0,
                    energy=energy,
                    status="completed",
                    geometry_ref=ts_ref,
                    metadata={"direction": "ts", "energy_raw": ts_energy},
                ).to_node(spec.node_type)
            )
            continue
        if frame is None:
            continue
        signed_x = (
            float(frame.index + 1) if direction == "forward" else float(-(frame.index + 1))
        )
        energy = (
            None if frame.energy_raw is None or reference is None else frame.energy_raw - reference
        )
        slots[(direction, frame.index)] = len(nodes)
        nodes.append(
            TrajectoryFrame(
                frame_id=f"irc_{direction}_{frame.index}",
                label=f"IRC {_DIRECTION_LABELS_ZH[direction]} {frame.index + 1}",
                frame_index=frame.index,
                x=signed_x,
                energy=energy,
                status=frame.status or ("completed" if frame.energy_raw is not None else "unknown"),
                geometry_ref=frame.geometry_ref,
                step=frame.index,
                metadata={
                    "direction": direction,
                    "energy_raw": frame.energy_raw,
                    "signed_step": signed_x,
                },
            ).to_node(spec.node_type)
        )

    forward_values: list[float | None] = [None] * len(nodes)
    reverse_values: list[float | None] = [None] * len(nodes)
    path_values: list[float | None] = [None] * len(nodes)
    for slot, node in enumerate(nodes):
        path_values[slot] = node["energy"]
        direction = str(node["metadata"].get("direction") or "")
        if direction == "forward":
            forward_values[slot] = node["energy"]
        elif direction == "reverse":
            reverse_values[slot] = node["energy"]

    series: list[dict[str, Any]] = []
    for series_id, label, values in (
        ("irc_path", "全部（TS 居中）", path_values),
        ("irc_forward", _DIRECTION_LABELS_ZH["forward"], forward_values),
        ("irc_reverse", _DIRECTION_LABELS_ZH["reverse"], reverse_values),
    ):
        if any(value is not None for value in values):
            series.append(
                {"id": series_id, "label": label, "unit": "Eh", "axis": "left", "values": values}
            )

    annotations: list[dict[str, Any]] = []
    if reference is not None:
        for direction in IRC_DIRECTIONS:
            frames = per_direction.get(direction) or []
            if not frames:
                continue
            values = [frame.energy_raw for frame in frames]
            finite_dir = [value for value in values if value is not None]
            if not finite_dir:
                continue
            if values[-1] is not None and values[-1] == min(finite_dir):
                last = frames[-1]
                slot = slots.get((direction, last.index))
                if slot is None:
                    continue
                node = nodes[slot]
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

    default_series = (
        "irc_path"
        if any(item["id"] == "irc_path" for item in series)
        else (series[0]["id"] if series else "")
    )

    return {
        "job_id": job_id,
        "view_type": "irc",
        "title": spec.title_zh,
        "status": status or "completed",
        "complete": bool(complete),
        "revision": "",
        "default_series": default_series,
        "available_views": ["irc"],
        "x_axis": {"label": spec.x_label_zh, "unit": spec.x_unit},
        "series": series,
        "nodes": nodes,
        "edges": [],
        "annotations": annotations,
        "source": source,
        "provenance": {},
        "metadata": {
            "frame_count": len(nodes),
            "directions": {d: len(per_direction[d]) for d in per_direction},
            "energy_available": bool(finite),
            "warnings": notes,
            "live": bool(live),
            "signed_x": True,
            "energy_reference": reference_kind,
            "ts_energy_hartree": ts_energy,
        },
    }
