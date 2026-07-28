"""
Core Package
============

Core conformer search modules.
"""

from cccp.core.candidates import (
    CandidateSet,
    ConformerCandidate,
    candidate_set_from_paths,
    clone_candidate_set,
)
from cccp.core.engine import ConformerEngine
from cccp.core.protocols import (
    SUPPORTED_PROTOCOLS,
    FunnelPolicy,
    HandoffPolicy,
    ProtocolSpec,
    get_protocol_expected_methods,
    is_ext_protocol,
    is_full_protocol,
    is_lite_protocol,
    is_zero_protocol,
    resolve_protocol_spec,
    validate_protocol_methods,
)
from cccp.core.state_manager import ConformerStateManager

__all__ = [
    "ProtocolSpec",
    "FunnelPolicy",
    "HandoffPolicy",
    "SUPPORTED_PROTOCOLS",
    "resolve_protocol_spec",
    "get_protocol_expected_methods",
    "validate_protocol_methods",
    "is_ext_protocol",
    "is_full_protocol",
    "is_lite_protocol",
    "is_zero_protocol",
    "ConformerCandidate",
    "CandidateSet",
    "candidate_set_from_paths",
    "clone_candidate_set",
    "ConformerStateManager",
    "ConformerEngine",
]
