"""Refinement-policy selection (§3.3).

Relocated verbatim from ``acp/confsearch/selection.py`` (plan todo 21, C04-s2;
T5 write-registry §4.4 ``C直写`` 移交) with one deliberate de-duplication: the
cumulative-weight cutoff loop is factored into
:func:`cumulative_selection_count`, the **canonical** implementation shared
with ``acp.confsearch.shared.helpers::select_cumulative_boltzmann`` (see that
function's module for the recorded behaviour difference).

``refinement_policy`` never changes the sampling route — it only decides
which conformers of the finished screening table receive the fine-DFT
refinement.

Name note (T7 spec): this module lives in ``cccp.analysis.confsearch`` and is
unrelated to ``cccp/calculation/selection.py`` (backend selection).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from .contracts import REFINEMENT_POLICIES, ConformerEntry

logger = logging.getLogger(__name__)


def cumulative_selection_count(
    weights: Sequence[float | None],
    threshold: float,
    *,
    epsilon: float = 0.0,
) -> int:
    """Count the leading entries kept by a cumulative-weight cutoff.

    Canonical cutoff loop shared by the two Confsearch cumulative selectors:

    * :func:`select_for_refinement` (``cumulative-99``) uses ``epsilon=1e-9``.
    * ``acp.confsearch.shared.helpers::select_cumulative_boltzmann`` uses the
      historical plain ``epsilon=0.0``.

    The entry that crosses the threshold is included; a non-empty sequence
    always keeps at least one entry.  ``None`` weights contribute 0.0.
    """
    count = 0
    cumulative = 0.0
    for weight in weights:
        count += 1
        cumulative += float(weight) if weight is not None else 0.0
        if cumulative >= threshold - epsilon:
            break
    return count


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
    count = cumulative_selection_count(
        [entry.boltzmann_weight for entry in conformers],
        threshold,
        epsilon=1e-9,
    )
    return [entry.conf_id for entry in conformers[:count]]


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


__all__ = [
    "cumulative_selection_count",
    "select_for_refinement",
    "threshold_for_policy",
]
