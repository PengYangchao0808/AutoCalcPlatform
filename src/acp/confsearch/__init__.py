"""ACP Confsearch — the single conformer-search entry (S1).

Replaces the retired ``ensemble`` / ``energy`` / ``xtbmd_censo_energy``
workflows with one entry, four protocols, quality profiles, and refinement
policies (docs/ACP_Confsearch_Manual_Mechanism_Modification_Plan.md §3).
"""

from __future__ import annotations

from .contracts import (
    BACKENDS,
    CENSO_PROTOCOLS,
    CONFSEARCH_SCHEMA_VERSION,
    PROFILES,
    PROTOCOL_LABELS,
    PROTOCOLS,
    PURE_XTB_PROTOCOLS,
    REFINEMENT_POLICIES,
    ConformerEntry,
    ConfsearchRequest,
    ConfsearchResult,
    ProtocolOutcome,
    validate_request,
)
from .engine import ConfsearchEngine
from .manifest import (
    MANIFEST_FILENAME,
    find_confsearch_manifest,
    read_manifest,
    representative_conformer,
    resolve_manifest_geometry,
)
from .selection import select_for_refinement

__all__ = [
    "BACKENDS",
    "CONFSEARCH_SCHEMA_VERSION",
    "CENSO_PROTOCOLS",
    "MANIFEST_FILENAME",
    "ConformerEntry",
    "ConfsearchEngine",
    "ConfsearchRequest",
    "ConfsearchResult",
    "PROFILES",
    "PROTOCOLS",
    "PROTOCOL_LABELS",
    "PURE_XTB_PROTOCOLS",
    "ProtocolOutcome",
    "REFINEMENT_POLICIES",
    "find_confsearch_manifest",
    "read_manifest",
    "representative_conformer",
    "resolve_manifest_geometry",
    "select_for_refinement",
    "validate_request",
]
