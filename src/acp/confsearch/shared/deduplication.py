"""Geometric conformer deduplication for the pure-xTB sampling layer.

ISOSTAT already deduplicates inside the ``xtb-md``/``xtbmd-censo``
protocols; this module provides the cheap in-process fallback (plain RMSD
after centroid alignment) used when clustering output needs a final
geometric pass, e.g. for CREST ensembles on the ``xtb-crest`` protocol.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


def _centered(coordinates: NDArray[np.float64]) -> NDArray[np.float64]:
    return coordinates - coordinates.mean(axis=0)


def plain_rmsd(a: NDArray[np.float64], b: NDArray[np.float64]) -> float:
    """Centroid-aligned RMSD between two same-shape coordinate blocks."""
    if a.shape != b.shape or a.size == 0:
        return float("inf")
    ac, bc = _centered(a), _centered(b)
    diff = ac - bc
    return float(np.sqrt((diff * diff).sum() / a.shape[0]))


def dedup_by_rmsd(
    records: list[dict[str, Any]],
    rmsd_threshold: float = 0.125,
) -> list[dict[str, Any]]:
    """Drop records geometrically duplicate of an earlier (lower-rank) one.

    Args:
        records: Rows with ``coordinates`` (list[list[float]] or ndarray),
            already ordered best-first.
        rmsd_threshold: RMSD (Å) below which two structures are duplicates.

    Returns:
        The retained records, order preserved.
    """
    kept_blocks: list[NDArray[np.float64]] = []
    kept: list[dict[str, Any]] = []
    for record in records:
        coordinates = record.get("coordinates")
        if coordinates is None:
            kept.append(record)
            continue
        block = np.asarray(coordinates, dtype=float)
        if any(plain_rmsd(block, other) < rmsd_threshold for other in kept_blocks):
            continue
        kept_blocks.append(block)
        kept.append(record)
    dropped = len(records) - len(kept)
    if dropped:
        logger.info("RMSD dedup removed %d duplicate conformers", dropped)
    return kept


__all__ = ["dedup_by_rmsd", "plain_rmsd"]
