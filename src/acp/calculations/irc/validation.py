"""Compat re-export shell — implementation moved to cccp (plan todo 21).

The pure geometry/connectivity endpoint classification (and the pure TS
identity judgment) now live in :mod:`cccp.calculation.irc_endpoints`.  This
module keeps the historical import surface working; it carries no
implementation logic.
"""

from __future__ import annotations

from cccp.calculation.irc_endpoints import (
    EndpointClassification,
    EndpointMatchThresholds,
    TsIdentity,
    classify_endpoint_geometry,
    classify_ts_identity,
    connectivity_fingerprint,
    mapped_heavy_atom_rmsd,
    perceive_connectivity,
)

__all__ = [
    "EndpointClassification",
    "EndpointMatchThresholds",
    "TsIdentity",
    "classify_endpoint_geometry",
    "classify_ts_identity",
    "connectivity_fingerprint",
    "mapped_heavy_atom_rmsd",
    "perceive_connectivity",
]
