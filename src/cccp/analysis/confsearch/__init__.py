"""CCCP Confsearch pure-science analysis (Boltzmann / selection / results).

Relocated from ``acp/confsearch`` by plan todo 21 (C04-s2):

* :mod:`.boltzmann` — Boltzmann weights + relative energies (``shared/boltzmann.py``).
* :mod:`.contracts` — the science-consumed contract symbols (``REFINEMENT_POLICIES``,
  ``ConformerEntry``, ``ProtocolOutcome``, ``PURE_XTB_PROTOCOLS``).
* :mod:`.selection` — refinement-policy selection + the canonical cumulative
  cutoff core (``selection.py``).
* :mod:`.result_helpers` — the protocol-outcome → manifest entry transformations
  (``result_helpers.py``).

No ``acp`` imports (``cccp`` must never import ``acp``).  The ACP compatibility
surface lives at ``acp.confsearch.shared.boltzmann`` / ``acp.confsearch.selection``
(re-export shims) and ``acp.confsearch.contracts`` /
``acp.confsearch.result_helpers`` (re-export patches) and resolves the same
objects.

``__init__`` carries re-exports only (no implementation) per repo ANTI #5.
"""

from __future__ import annotations

from cccp.analysis.confsearch.boltzmann import (
    HARTREE_TO_KCAL,
    boltzmann_weights,
    relative_energies_kcal,
)
from cccp.analysis.confsearch.contracts import (
    PURE_XTB_PROTOCOLS,
    REFINEMENT_POLICIES,
    ConformerEntry,
    ProtocolOutcome,
)
from cccp.analysis.confsearch.result_helpers import (
    build_entries,
    quality_gates,
    refinement_block,
    sorted_records,
)
from cccp.analysis.confsearch.selection import (
    cumulative_selection_count,
    select_for_refinement,
    threshold_for_policy,
)

__all__ = [
    "HARTREE_TO_KCAL",
    "PURE_XTB_PROTOCOLS",
    "REFINEMENT_POLICIES",
    "ConformerEntry",
    "ProtocolOutcome",
    "boltzmann_weights",
    "build_entries",
    "cumulative_selection_count",
    "quality_gates",
    "refinement_block",
    "relative_energies_kcal",
    "select_for_refinement",
    "sorted_records",
    "threshold_for_policy",
]
