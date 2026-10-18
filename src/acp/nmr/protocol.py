"""Compatibility shim — NMR ``protocol`` science now lives in
``cccp.analysis.nmr.protocol`` (plan C05-s4 / T28).

Re-exports the same objects so existing ``acp.nmr.protocol`` imports and
``isinstance``/identity checks keep working. Isolated optional-research
seams remain outside this path."""

from __future__ import annotations

from cccp.analysis.nmr.protocol import (
    PROTOCOL_MODES,
    SPEC_VERSION,
    UNVALIDATED_PROTOCOL,
    GeometrySegment,
    NmrProtocolSpec,
    PopulationEnergySegment,
    ProtocolMode,
    ReferenceSegment,
    SamplingSegment,
    ShieldingSegment,
    StatisticalModelSegment,
    aggregate_protocol_block,
    build_protocol_spec,
    classify_tms_source,
)

__all__ = [
    "GeometrySegment",
    "NmrProtocolSpec",
    "PROTOCOL_MODES",
    "PopulationEnergySegment",
    "ProtocolMode",
    "ReferenceSegment",
    "SPEC_VERSION",
    "SamplingSegment",
    "ShieldingSegment",
    "StatisticalModelSegment",
    "UNVALIDATED_PROTOCOL",
    "aggregate_protocol_block",
    "build_protocol_spec",
    "classify_tms_source",
]
