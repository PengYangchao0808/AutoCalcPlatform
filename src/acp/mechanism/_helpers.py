"""Shared micro-helpers for the mechanism package.

Consolidates near-identical private helpers that were previously duplicated
across the mechanism modules: atomic JSON writes, numeric coercion, backend
resolution, stable-state geometry extraction, atom-mapping construction, and
inter-atomic distance.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

from cccp.utils.geometry_tools import GeometryUtils

logger = logging.getLogger(__name__)


def opt_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def opt_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def opt_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def next_sequence(root: Path, pattern: str) -> int:
    """Next zero-based index after the max trailing integer in ``root.glob(pattern)`` dirs."""
    highest = -1
    try:
        candidates = list(root.glob(pattern))
    except OSError:
        return 0
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        match = re.search(r"(\d+)$", candidate.name)
        if match is not None:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        dir=str(path.parent),
        suffix=".tmp",
        delete=False,
        mode="w",
        encoding="utf-8",
    )
    try:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.close()
        os.replace(handle.name, path)
    except Exception:
        handle.close()
        try:
            os.unlink(handle.name)
        except OSError:
            logger.exception("Failed to remove temporary file %s", handle.name)
        raise


def resolve_backend(backend_spec: str | Any, config: dict[str, Any]) -> Any:
    if not isinstance(backend_spec, str):
        return backend_spec
    from acp.backends.registry import get_backend

    try:
        backend_cls = get_backend(backend_spec)
    except KeyError:
        import acp.backends  # noqa: F401

        backend_cls = get_backend(backend_spec)
    return backend_cls(config)


def backend_name(backend_spec: str | Any) -> str:
    if isinstance(backend_spec, str):
        return backend_spec
    return type(backend_spec).__name__


def state_geometry(state: Any) -> tuple[NDArray[np.float64], list[str]]:
    if state.ensemble is not None:
        representative = state.ensemble.global_minimum()
        if representative is not None and representative.coordinates is not None:
            return np.asarray(representative.coordinates, dtype=float), list(representative.symbols)
    coordinates = state.metadata.get("coordinates")
    symbols = state.metadata.get("symbols")
    if coordinates is None or symbols is None:
        raise ValueError(
            f"StableState {state.state_id!r} requires ensemble coordinates or metadata "
            "coordinates/symbols"
        )
    return np.asarray(coordinates, dtype=float), [str(symbol) for symbol in symbols]


def mapping_pairs_from_occurrence(
    candidate_symbols: Sequence[str],
    reference_symbols: Sequence[str],
) -> list[tuple[int, int]] | None:
    if len(candidate_symbols) != len(reference_symbols):
        return None
    if Counter(candidate_symbols) != Counter(reference_symbols):
        return None
    slots: dict[str, list[int]] = {}
    for index, symbol in enumerate(reference_symbols):
        slots.setdefault(symbol, []).append(index)
    offsets: dict[str, int] = {}
    pairs: list[tuple[int, int]] = []
    for candidate_index, symbol in enumerate(candidate_symbols):
        reference_indices = slots[symbol]
        offset = offsets.get(symbol, 0)
        pairs.append((candidate_index, reference_indices[offset]))
        offsets[symbol] = offset + 1
    return pairs


def distance(coordinates: NDArray[np.float64], atom_i: int, atom_j: int) -> float:
    return GeometryUtils.calculate_distance(coordinates, atom_i, atom_j)


__all__ = [
    "backend_name",
    "distance",
    "mapping_pairs_from_occurrence",
    "next_sequence",
    "opt_float",
    "opt_int",
    "opt_str",
    "resolve_backend",
    "state_geometry",
    "write_json_atomic",
]
