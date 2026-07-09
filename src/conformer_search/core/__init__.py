"""
Core Package
============

Core conformer search modules.
"""

from conformer_search.core.protocols import (
    ProtocolSpec,
    FunnelPolicy,
    HandoffPolicy,
    SUPPORTED_PROTOCOLS,
    resolve_protocol_name,
    resolve_protocol_spec,
    is_censo_protocol,
    is_ext_protocol,
    is_full_protocol,
    is_lite_protocol,
    is_zero_protocol,
    is_benchmark_protocol,
)

from conformer_search.core.spec_adapter import (
    ALIASES,
    resolve_any_protocol,
    workflow_spec_to_protocol_spec,
    workflow_spec_to_config_overrides,
    stages_from_workflow_spec,
)

from conformer_search.core.candidates import (
    ConformerCandidate,
    CandidateSet,
    candidate_set_from_paths,
    clone_candidate_set,
)

from conformer_search.core.state_manager import ConformerStateManager

from conformer_search.core.engine import ConformerEngine

__all__ = [
    "ProtocolSpec",
    "FunnelPolicy",
    "HandoffPolicy",
    "SUPPORTED_PROTOCOLS",
    "resolve_protocol_name",
    "resolve_protocol_spec",
    "is_censo_protocol",
    "is_ext_protocol",
    "is_full_protocol",
    "is_lite_protocol",
    "is_zero_protocol",
    "is_benchmark_protocol",
    "ALIASES",
    "resolve_any_protocol",
    "workflow_spec_to_protocol_spec",
    "workflow_spec_to_config_overrides",
    "stages_from_workflow_spec",
    "ConformerCandidate",
    "CandidateSet",
    "candidate_set_from_paths",
    "clone_candidate_set",
    "ConformerStateManager",
    "ConformerEngine",
]
