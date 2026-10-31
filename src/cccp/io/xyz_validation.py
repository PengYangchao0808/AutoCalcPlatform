"""Strict single-frame XYZ validation (round-5 PR-02).

The generic ``read_xyz`` intentionally reads the first frame of a trajectory
(historical behaviour, unchanged).  IRC endpoint evidence must not: an ORCA
trajectory or a concatenated multi-frame file handed to the endpoint reader
used to be silently reduced to its first frame and then materialized as a
formal ``irc_f.xyz`` endpoint.  This module is the strict reader for evidence
files — exactly one complete frame, no trailing content, finite coordinates.
"""

from __future__ import annotations

import math
from pathlib import Path

__all__ = ["SingleFrameXyzError", "read_single_frame_xyz"]


class SingleFrameXyzError(ValueError):
    """The file is not exactly one complete, finite XYZ frame."""


def read_single_frame_xyz(path: str | Path) -> tuple[list[list[float]], list[str]]:
    """Read one complete XYZ frame; reject multi-frame/partial/non-finite files.

    Returns:
        ``(coordinates, symbols)`` where coordinates are lists of three floats.

    Raises:
        SingleFrameXyzError: empty file, bad header, truncated frame, trailing
            content (multi-frame), malformed atom line, or non-finite coordinate.
        OSError: the file cannot be read.
    """
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        raise SingleFrameXyzError("empty file")
    try:
        count = int(lines[0].strip())
    except ValueError as exc:
        raise SingleFrameXyzError("invalid atom-count header") from exc
    if count <= 0:
        raise SingleFrameXyzError("atom count must be positive")
    if len(lines) < count + 2:
        raise SingleFrameXyzError("truncated frame")
    if any(line.strip() for line in lines[count + 2 :]):
        # A trajectory (or any trailing block) is not an endpoint file.
        raise SingleFrameXyzError("multiple frames or trailing content")
    coordinates: list[list[float]] = []
    symbols: list[str] = []
    for line in lines[2 : 2 + count]:
        fields = line.split()
        if len(fields) != 4 or not fields[0]:
            raise SingleFrameXyzError("atom line must have 4 fields")
        try:
            values = [float(item) for item in fields[1:]]
        except ValueError as exc:
            raise SingleFrameXyzError("non-numeric coordinate") from exc
        if not all(math.isfinite(value) for value in values):
            raise SingleFrameXyzError("non-finite coordinate")
        symbols.append(fields[0])
        coordinates.append(values)
    return coordinates, symbols
