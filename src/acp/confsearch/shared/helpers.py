"""Confsearch shared helpers — ACP platform paths + CCCP science forwarding.

T50 retirement (plan-3 ``C10-s3``): the science bodies that T48 relocated here
are now **same-object forwards** to their CCCP single source (T24 landing).
Keeping a second ACP copy would violate the single-implementation invariant
(root ANTI #17 / write-registry §4.4).  Only the platform layout helpers
(``v2_stage_dir`` / ``v2_result_category``) stay local — they carry ACP's
``TaskStorage`` path policy and have no CCCP counterpart.

Forwarded (identity-preserving re-exports)::

    resolve_levels, _base_route_extras, _solvent_from_level
        ← cccp.analysis.ensemble.levels
    resolve_crest_ewin
        ← cccp.analysis.ensemble.solvent
    xtb_passthrough_result
        ← cccp.calculation.energy_adapters

``select_cumulative_boltzmann`` is deliberately **not** forwarded: the CCCP
retired-engine copy (:func:`cccp.analysis.ensemble.selection.select_cumulative_boltzmann`)
normalises through a different R constant than the T21 Boltzmann weights this
caller consumes, so boundary selections diverge.  T24 §5 recorded the
non-merge; T50 re-confirmed it on a fixed boundary input (evidence
``t50-equivalence-pre.txt``).  The body below stays thin glue over the cccp
primitives (``cumulative_selection_count`` + the T21 weights).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from acp.storage.layout import TaskStorage
from cccp.analysis.confsearch.selection import cumulative_selection_count
from cccp.analysis.ensemble.levels import (
    _base_route_extras,  # noqa: F401  (re-exported: test/public compatibility)
    _solvent_from_level,  # noqa: F401  (re-exported: test/public compatibility)
    resolve_levels,
)
from cccp.analysis.ensemble.solvent import resolve_crest_ewin
from cccp.calculation.energy_adapters import xtb_passthrough_result


# ---------------------------------------------------------------------------
# v2 stage / result helpers (ACP platform layout — no CCCP counterpart)
# ---------------------------------------------------------------------------


def v2_stage_dir(mol_dir: Path, stage: str, engine: str | None = None) -> Path:
    """Resolve (and create) ``WORK/<stage>/<engine>`` under *mol_dir* (v2 layout)."""
    storage = TaskStorage(mol_dir)
    path = storage.stage_dir(stage, engine)
    path.mkdir(parents=True, exist_ok=True)
    return path


def v2_result_category(mol_dir: Path, category: str) -> Path:
    """Resolve (and create) ``RESULT/<category>`` under *mol_dir* (v2 layout)."""
    storage = TaskStorage(mol_dir)
    path = storage.result_category_dir(category)
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Cumulative Boltzmann selection (T21 weights + plain cutoff, kept local)
# ---------------------------------------------------------------------------


def select_cumulative_boltzmann(
    records: list[Any],
    temperature_k: float,
    threshold: float,
) -> list[Any]:
    """Select the lowest-gtot conformers up to a cumulative Boltzmann cutoff.

    Records (``.gtot``-bearing, e.g. :class:`CensoConformerRecord`) are
    sorted by ascending ``gtot``; conformers are accumulated until their
    cumulative Boltzmann population reaches ``threshold`` (the conformer
    crossing the threshold is included). Rank1 is always selected.

    Canonical cutoff loop (T21): the accumulate-until-threshold loop is
    :func:`cccp.analysis.confsearch.selection.cumulative_selection_count`.
    This caller keeps the historical plain cutoff (``epsilon=0.0``), while
    ``select_for_refinement("cumulative-99", ...)`` uses ``epsilon=1e-9`` —
    the two thresholds remain behaviourally distinct by design.  The weights
    use the T21 Boltzmann normalisation (``acp.confsearch.shared.boltzmann``),
    which is why this is NOT the same function as
    :func:`cccp.analysis.ensemble.selection.select_cumulative_boltzmann`
    (different R constant; see module docstring).
    """
    from .boltzmann import boltzmann_weights as _bw

    if not records:
        return []
    ordered = sorted(records, key=lambda r: r.gtot)
    weights = _bw([r.gtot for r in ordered], temperature_k)
    count = cumulative_selection_count(weights, threshold, epsilon=0.0)
    return ordered[:count]


__all__ = [
    "resolve_crest_ewin",
    "resolve_levels",
    "v2_result_category",
    "v2_stage_dir",
    "xtb_passthrough_result",
]
