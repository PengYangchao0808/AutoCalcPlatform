"""Frame normalization and cache identity helpers."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from acp.calculations.batch._items import BatchStructureItem
from acp.calculations.contracts import StructureArtifact
from acp.core.models import Structure
from cccp.utils.file_io import read_xyz

from ._singlepoint_models import BatchSinglePointFrame, FloatGeometry, FrameInput


def frame_id(frame: FrameInput, index: int, frame_ids: Sequence[str] | None) -> str:
    if frame_ids is not None:
        return str(frame_ids[index])
    if isinstance(frame, (BatchSinglePointFrame, Structure, BatchStructureItem)):
        value = (
            getattr(frame, "frame_id", None)
            or getattr(frame, "item_id", None)
            or getattr(frame, "id", None)
        )
        if value:
            return str(value)
    if isinstance(frame, StructureArtifact):
        return frame.path.stem or f"frame_{index:03d}"
    if isinstance(frame, (Path, str)):
        return Path(frame).stem or f"frame_{index:03d}"
    value = getattr(frame, "point_id", None)
    return str(value) if value else f"frame_{index:03d}"


def frame_data(
    frame: FrameInput,
    frame_id_value: str,
    symbols: Sequence[str] | None,
    charge: int | None,
    multiplicity: int | None,
) -> tuple[NDArray[np.float64], list[str], int, int, str, str, str]:
    tag = "INT"
    candidate_id = frame_id_value
    frame_xyz: str | None = None
    frame_charge = charge
    frame_multiplicity = multiplicity
    if isinstance(frame, BatchSinglePointFrame):
        coordinates = frame.coordinates
        frame_symbols = list(frame.symbols)
        frame_charge = frame.charge if charge is None else charge
        frame_multiplicity = frame.multiplicity if multiplicity is None else multiplicity
        tag, candidate_id = frame.tag, frame.candidate_id or frame_id_value
    elif isinstance(frame, Structure):
        if frame.coordinates is None:
            raise ValueError(f"frame {frame_id_value} has no coordinates")
        coordinates = frame.coordinates
        frame_symbols = list(frame.symbols)
        frame_charge = frame.charge if charge is None else charge
        frame_multiplicity = frame.multiplicity if multiplicity is None else multiplicity
        tag = str(frame.metadata.get("tag") or "INT")
        candidate_id = str(frame.metadata.get("candidate_id") or frame_id_value)
    elif isinstance(frame, BatchStructureItem):
        if not frame.xyz.strip():
            raise ValueError(f"frame {frame_id_value} has no XYZ geometry")
        coordinates, frame_symbols = read_xyz_text(frame.xyz)
        frame_charge = frame.resolved_charge(0) if charge is None else charge
        frame_multiplicity = (
            frame.resolved_multiplicity(1) if multiplicity is None else multiplicity
        )
        tag, candidate_id, frame_xyz = frame.tag, frame.candidate_id or frame_id_value, frame.xyz
    elif isinstance(frame, StructureArtifact):
        coordinates, parsed_symbols = read_xyz(frame.path)
        frame_symbols = list(frame.elements) or list(parsed_symbols)
    elif isinstance(frame, (Path, str)):
        coordinates, frame_symbols = read_xyz(Path(frame))
    else:
        coordinates = getattr(frame, "geometry", frame)
        frame_symbols = list(getattr(frame, "symbols", symbols or ()))
    normalized = np.asarray(coordinates, dtype=np.float64)
    if normalized.ndim != 2 or normalized.shape[1] != 3:
        raise ValueError(f"frame {frame_id_value} geometry must have shape (N, 3)")
    final_symbols = list(symbols) if symbols is not None else frame_symbols
    if len(final_symbols) != normalized.shape[0]:
        raise ValueError(f"frame {frame_id_value} geometry and symbols have different atom counts")
    final_charge = 0 if frame_charge is None else int(frame_charge)
    final_multiplicity = 1 if frame_multiplicity is None else int(frame_multiplicity)
    return (
        normalized,
        final_symbols,
        final_charge,
        final_multiplicity,
        tag,
        candidate_id,
        frame_xyz or xyz_text(normalized, final_symbols),
    )


def method_signature(
    backend: str,
    method: str | None,
    basis: str | None,
    solvent: str | None,
    options: Mapping[str, object],
) -> str:
    payload = {
        "backend": backend,
        "method": method,
        "basis": basis,
        "solvent": solvent,
        "options": {key: str(value) for key, value in sorted(options.items())},
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def scope(keys: Sequence[str]) -> str:
    payload = json.dumps(list(keys), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def read_xyz_text(text: str) -> tuple[NDArray[np.float64], list[str]]:
    lines = text.strip().splitlines()
    if len(lines) < 2:
        raise ValueError("XYZ text must contain an atom count and title")
    try:
        count = int(lines[0].strip())
    except ValueError as exc:
        raise ValueError("XYZ atom count is not an integer") from exc
    rows = lines[2 : count + 2]
    if len(rows) != count:
        raise ValueError("XYZ text is missing atom rows")
    parsed_symbols: list[str] = []
    coordinates: list[list[float]] = []
    for row in rows:
        fields = row.split()
        if len(fields) < 4:
            raise ValueError("XYZ atom row is malformed")
        parsed_symbols.append(fields[0])
        coordinates.append([float(fields[1]), float(fields[2]), float(fields[3])])
    return np.asarray(coordinates, dtype=np.float64), parsed_symbols


def xyz_text(coordinates: FloatGeometry, symbols: Sequence[str]) -> str:
    normalized = np.asarray(coordinates, dtype=np.float64)
    rows = [str(len(symbols)), "frame"]
    rows.extend(
        f"{symbol} {float(row[0]):.10f} {float(row[1]):.10f} {float(row[2]):.10f}"
        for symbol, row in zip(symbols, normalized, strict=True)
    )
    return "\n".join(rows) + "\n"


__all__ = ["frame_data", "frame_id", "method_signature", "read_xyz_text", "scope", "xyz_text"]
