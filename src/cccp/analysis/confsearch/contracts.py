"""CCCP Confsearch science contracts (plan todo 21, C04-s2).

Single-source home for the Confsearch symbols consumed by the pure-science
algorithms relocated from ``acp/confsearch``:

* :data:`REFINEMENT_POLICIES` — fine-refinement scope vocabulary (§3.3).
* :class:`ConformerEntry` — one conformer row of the unified manifest (§5).
* :class:`ProtocolOutcome` — normalized protocol output consumed by the entry
  builder / quality gates.
* :data:`PURE_XTB_PROTOCOLS` — the pure-xTB protocol tuple the quality gates
  and refinement block branch on.

**T20 ruling + T21 amendment.** T20 (``t20-contract-ruling.md``) ruled
``REFINEMENT_POLICIES`` / ``ConformerEntry`` / ``ProtocolOutcome`` **MOVE** here
(``acp/confsearch/contracts.py`` stays a ``B补丁`` file and re-exports the
same objects).  ``PURE_XTB_PROTOCOLS`` is required by the relocated
``result_helpers`` science, so it moves with them (T20 §1 omitted it from the
"consumed by science" table; keeping it ACP-owned would force a second
definition in ``cccp`` — ANTI #9).  All four are re-exported same-object from
``acp.confsearch.contracts`` by the T21 patch.

This module never imports ``acp``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

REFINEMENT_POLICIES: tuple[str, ...] = ("screen", "rank1", "cumulative-99", "all")
"""Fine-refinement scope (§3.3): screen = protocol screening only, rank1 =
lowest conformer, cumulative-99 = cumulative Boltzmann ≥99%, all = every
retained conformer."""

PURE_XTB_PROTOCOLS: tuple[str, ...] = ("xtb-crest", "xtb-md")


@dataclass
class ConformerEntry:
    """One conformer row of the unified manifest (§5).

    ``conf_id`` is the manifest-local ``conf_NNNN`` identifier; the additive
    ``source_conf_id`` carries the original screening-table key (e.g. the
    CENSO ``CONF1`` id) used to join ``boltzmann_table.json``.  ``weight_source``
    names where the Boltzmann weight came from (``censo`` / ``xtb`` / ``dft``
    / ``computed``) and ``refined`` marks entries that received the fine-DFT
    refinement.
    """

    conf_id: str
    geometry: str
    energy_hartree: float | None = None
    free_energy_hartree: float | None = None
    relative_energy_kcal: float | None = None
    boltzmann_weight: float | None = None
    rank: int = 0
    source_conf_id: str | None = None
    weight_source: str | None = None
    refined: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "conf_id": self.conf_id,
            "geometry": self.geometry,
            "energy_hartree": self.energy_hartree,
            "free_energy_hartree": self.free_energy_hartree,
            "relative_energy_kcal": self.relative_energy_kcal,
            "boltzmann_weight": self.boltzmann_weight,
            "rank": self.rank,
            "source_conf_id": self.source_conf_id,
            "weight_source": self.weight_source,
            "refined": self.refined,
        }


@dataclass
class ProtocolOutcome:
    """Normalized protocol output consumed by :class:`ConfsearchEngine`."""

    records: list[dict[str, Any]]
    """Each row: ``conf_id``, optional ``source_conf_id`` (screening-table
    join key), ``symbols``, ``coordinates``, ``energy_hartree``,
    ``free_energy_hartree``, ``weight``, optional ``properties``."""

    temperature_k: float = 298.15
    refined_conf_ids: list[str] = field(default_factory=list)
    sampling: dict[str, Any] = field(default_factory=dict)
    stages_completed: list[str] = field(default_factory=list)
    workflow_metadata: dict[str, Any] = field(default_factory=dict)
    weight_table: dict[str, float] | None = None
    """Authoritative screening weights keyed by ``source_conf_id``
    (e.g. ``{"CONF1": 0.8442}``); ``None`` keeps the recomputed path."""
    weight_source: str | None = None
    """Where the authoritative weights came from: ``censo`` / ``xtb`` /
    ``dft`` / ``computed`` (degraded)."""
    weight_method: str | None = None
    """Provenance method label (e.g. ``censo_table_rank1``)."""
    population_coverage: float | None = None
    """Fraction of the screening population covered by the table."""
    energy_kind: str | None = None
    """Energy model of the reported energies (e.g. ``dft`` / ``censo`` /
    ``xtb``)."""


__all__ = [
    "PURE_XTB_PROTOCOLS",
    "REFINEMENT_POLICIES",
    "ConformerEntry",
    "ProtocolOutcome",
]
