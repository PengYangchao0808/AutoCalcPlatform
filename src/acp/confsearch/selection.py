"""Refinement-policy selection (§3.3).

``refinement_policy`` never changes the sampling route — it only decides
which conformers of the finished screening table receive the fine-DFT
refinement.
"""

from __future__ import annotations

import logging

from .contracts import REFINEMENT_POLICIES, ConformerEntry

logger = logging.getLogger(__name__)


def select_for_refinement(
    policy: str,
    conformers: list[ConformerEntry],
    threshold: float = 0.99,
) -> list[str]:
    """Return the conf_ids selected for fine refinement under *policy*.

    Args:
        policy: One of :data:`REFINEMENT_POLICIES`.
        conformers: Ranked conformer entries (rank 1 = lowest energy).
        threshold: Cumulative Boltzmann cutoff for ``cumulative-99``.

    Returns:
        Selected ``conf_id`` values, best-first.
    """
    if policy not in REFINEMENT_POLICIES:
        raise ValueError(f"Unknown refinement_policy {policy!r}")
    if not conformers:
        return []
    if policy == "screen":
        return []
    if policy == "rank1":
        return [conformers[0].conf_id]
    if policy == "all":
        return [entry.conf_id for entry in conformers]
    # cumulative-99: keep conformers until the cumulative Boltzmann weight
    # of the ranked table reaches the threshold (always ≥ rank 1).
    selected: list[str] = []
    cumulative = 0.0
    for entry in conformers:
        selected.append(entry.conf_id)
        cumulative += entry.boltzmann_weight or 0.0
        if cumulative >= threshold - 1e-9:
            break
    return selected


def threshold_for_policy(policy: str, default: float = 0.99) -> float | None:
    """Map a policy onto the delegated workflow's threshold knob.

    ``cumulative-99`` uses *default*; ``all`` forces 1.0; the other
    policies do not use a threshold (``None``).
    """
    if policy == "all":
        return 1.0
    if policy == "cumulative-99":
        return default
    return None


__all__ = ["select_for_refinement", "threshold_for_policy"]
