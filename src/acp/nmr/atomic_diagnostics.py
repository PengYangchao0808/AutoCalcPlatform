"""Compatibility shim — NMR ``atomic_diagnostics`` science now lives in
``cccp.analysis.nmr.atomic_diagnostics`` (plan C05-s4 / T28).

Re-exports the same objects so existing ``acp.nmr.atomic_diagnostics`` imports and
``isinstance``/identity checks keep working. Isolated optional-research
seams remain outside this path."""

from __future__ import annotations

from cccp.analysis.nmr.atomic_diagnostics import (
    CALIBRATED_NAMESPACE,
    DIAGNOSTICS_KIND,
    RISK_NAMESPACE,
    AtomDp5Support,
    AtomicDiagnosticsBundle,
    CandidateAtomicDiagnostics,
    ConflictMatrix,
    Dp4Decomposition,
    LeaveOneSignalOut,
    NucleusDp4,
    SignalClaim,
    SignalRiskRecord,
    aggregate_atom_support,
    build_atomic_diagnostics,
)

__all__ = [
    "AtomDp5Support",
    "AtomicDiagnosticsBundle",
    "CALIBRATED_NAMESPACE",
    "CandidateAtomicDiagnostics",
    "ConflictMatrix",
    "DIAGNOSTICS_KIND",
    "Dp4Decomposition",
    "LeaveOneSignalOut",
    "NucleusDp4",
    "RISK_NAMESPACE",
    "SignalClaim",
    "SignalRiskRecord",
    "aggregate_atom_support",
    "build_atomic_diagnostics",
]
