"""Unified conformer-search contracts (Confsearch, S1).

The Confsearch workflow replaces the retired ``ensemble`` / ``energy`` /
``xtbmd_censo_energy`` entries with one entry and four protocols
(docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md §3):

* ``xtb-crest``  — CREST → GFN2-xTB energies → dedup → Boltzmann (pure xTB)
* ``xtb-md``     — GFN-FF/xTB MD → GFN1 opt → ISOSTAT dedup → Boltzmann (pure xTB)
* ``censo-crest``— CREST → CENSO → conformer free energies
* ``xtbmd-censo``— GFN-FF MD → GFN1 opt → ISOSTAT → CENSO → fine DFT

The orthogonal axes are ``profile`` (calculation quality inside one protocol)
and ``refinement_policy`` (which conformers receive the fine-DFT refinement).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CONFSEARCH_SCHEMA_VERSION = "confsearch_v1"

PROTOCOLS: tuple[str, ...] = ("xtb-crest", "xtb-md", "censo-crest", "xtbmd-censo")
"""Sampling/energy路线 identifiers (§3.2). Protocols must differ in the
sampling mechanism or the primary energy model — never just the name."""

PROTOCOL_LABELS: dict[str, str] = {
    "xtb-crest": "xTB + CREST",
    "xtb-md": "xTB-MD",
    "censo-crest": "CREST + CENSO",
    "xtbmd-censo": "xTB-MD + CENSO + DFT",
}

PROFILES: tuple[str, ...] = ("light", "default", "high")
"""Quality knobs inside one protocol (§3.4) — must NOT change the sampling
mechanism."""

REFINEMENT_POLICIES: tuple[str, ...] = ("screen", "rank1", "cumulative-99", "all")
"""Fine-refinement scope (§3.3): screen = protocol screening only, rank1 =
lowest conformer, cumulative-99 = cumulative Boltzmann ≥99%, all = every
retained conformer."""

BACKENDS: tuple[str, ...] = ("native",)
"""``native`` is the only production backend.  The ``rph-parity`` backend
was retired in the 2026-08 Wave 8 refactor — attempting to use it raises
:class:`ValueError` at config-parsing time."""

PURE_XTB_PROTOCOLS: tuple[str, ...] = ("xtb-crest", "xtb-md")
CENSO_PROTOCOLS: tuple[str, ...] = ("censo-crest", "xtbmd-censo")


@dataclass(frozen=True)
class ConfsearchRequest:
    """One Confsearch job request (§6.1)."""

    input_source: str
    output_dir: Path
    protocol: str = "censo-crest"
    profile: str = "default"
    refinement_policy: str = "screen"
    backend: str = "native"
    name: str | None = None
    charge: int | None = None
    multiplicity: int | None = None
    solvent: str | None = None
    nproc: int | None = None
    preset: str | None = None
    levels: dict[str, Any] | None = None
    temperature: float | None = None
    energy_window: float | None = None
    max_conformers: int | None = None
    md_params: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] | None = None


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
class ConfsearchResult:
    """Outcome of one Confsearch run."""

    status: str
    protocol: str
    profile: str
    refinement_policy: str
    conformers: list[ConformerEntry] = field(default_factory=list)
    selected_conformers: list[str] = field(default_factory=list)
    manifest_path: Path | None = None
    quality_gates: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


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


def validate_request(request: ConfsearchRequest) -> None:
    """Reject unknown protocol/profile/policy/backend values (§3, §4).

    Raises:
        ValueError: With a message naming the allowed values.
    """
    if request.protocol not in PROTOCOLS:
        raise ValueError(f"Unknown protocol {request.protocol!r}. Allowed: {', '.join(PROTOCOLS)}")
    if request.profile not in PROFILES:
        raise ValueError(f"Unknown profile {request.profile!r}. Allowed: {', '.join(PROFILES)}")
    if request.refinement_policy not in REFINEMENT_POLICIES:
        raise ValueError(
            f"Unknown refinement_policy {request.refinement_policy!r}. "
            f"Allowed: {', '.join(REFINEMENT_POLICIES)}"
        )
    if request.backend in ("rph-parity", "rph"):
        raise ValueError("RPH 已退役（2026-08 重构）：请使用 NATIVE provider")
    if request.backend not in BACKENDS:
        raise ValueError(f"Unknown backend {request.backend!r}. Allowed: {', '.join(BACKENDS)}")
    if request.protocol in PURE_XTB_PROTOCOLS and request.refinement_policy != "screen":
        logger.warning(
            "protocol=%s is pure xTB — refinement_policy=%s has no DFT stage to "
            "apply and is treated as 'screen'",
            request.protocol,
            request.refinement_policy,
        )


__all__ = [
    "BACKENDS",
    "CONFSEARCH_SCHEMA_VERSION",
    "CENSO_PROTOCOLS",
    "ConformerEntry",
    "ConfsearchRequest",
    "ConfsearchResult",
    "PROFILES",
    "PROTOCOLS",
    "PROTOCOL_LABELS",
    "PURE_XTB_PROTOCOLS",
    "ProtocolOutcome",
    "REFINEMENT_POLICIES",
    "validate_request",
]
