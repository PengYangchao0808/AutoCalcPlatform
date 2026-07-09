"""
Protocols
=========

Conformer search protocol definitions.

Author: QCcalc Team (adapted from RPH)
"""

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass(frozen=True)
class FunnelPolicy:
    """Funnel search policy parameters."""
    search_mode: str = "crest_gfn2"
    clustering_mode: str = "isostat"
    prescreen_mode: str = "none"
    rerank_mode: str = "none"
    use_mrrho_like_correction: bool = False
    survivor_window_kcal: Optional[float] = 3.0
    prescreen_window_kcal: Optional[float] = 4.0
    screening_window_kcal: Optional[float] = 3.5
    optimize_limit: Optional[int] = None
    top2_fallback_enabled: bool = False
    boltzmann_cutoff: Optional[float] = 0.90


@dataclass(frozen=True)
class HandoffPolicy:
    """Handoff policy parameters."""
    mode: str = "optimize_rank1"
    fallback_mode: Optional[str] = None
    small_gap_kcal: Optional[float] = 1.0
    ranking_after_handoff: str = "final_sp_minimum"


@dataclass(frozen=True)
class ProtocolSpec:
    """
    Complete protocol specification.
    
    Attributes:
        name: Protocol name (ext, censo-zero, censo-full, legacy-ext, reference-sp, ...)
        two_stage_enabled: Enable two-stage CREST search
        ngeom_default: Default number of geometries to optimize
        ngeom_max: Maximum number of geometries
        funnel_policy: Funnel search parameters
        handoff_policy: DFT handoff parameters
        final_sp_method: Final SP method
        final_sp_basis: Final SP basis set
        opt_engine: Optimization engine (gaussian, orca)
        freq_engine: Frequency engine (gaussian, orca)
        enable_crest: Enable CREST conformer search stage
        enable_clustering: Enable ISOSTAT clustering stage
        enable_optimization: Enable DFT geometry optimization stage
        enable_frequency: Enable frequency calculation stage
        enable_single_point: Enable high-precision single-point energy stage
        enable_shermo: Enable Shermo thermodynamic correction stage
    """
    name: str
    two_stage_enabled: bool = True
    ngeom_default: int = 3
    ngeom_max: int = 6
    funnel_policy: FunnelPolicy = field(default_factory=FunnelPolicy)
    handoff_policy: HandoffPolicy = field(default_factory=HandoffPolicy)
    final_sp_method: str = "wB97X-D4"
    final_sp_basis: str = "def2-TZVPP"
    opt_engine: str = "gaussian"
    freq_engine: str = "gaussian"
    enable_crest: bool = True
    enable_clustering: bool = True
    enable_optimization: bool = True
    enable_frequency: bool = True
    enable_single_point: bool = True
    enable_shermo: bool = True


SUPPORTED_PROTOCOLS = {
    "default",
    "ext",
    "censo-zero",
    "censo-lite",
    "censo-full",
    "censo-full-safe",
    "allopt",
    "reference-sp",
}

CENSO_PROTOCOLS = {
    "censo-zero",
    "censo-lite",
    "censo-full",
    "censo-full-safe",
}

# Config key fallbacks for CENSO-family protocols — now resolved directly.
PROTOCOL_CONFIG_FALLBACKS: dict[str, str] = {}


def resolve_protocol_name(
    config_or_protocol: Any,
    protocol: Optional[str] = None,
) -> str:
    """Resolve a requested protocol name, honoring the configured default."""
    if isinstance(config_or_protocol, dict):
        config = config_or_protocol
        requested = protocol
    else:
        config = {}
        requested = config_or_protocol if protocol is None else protocol

    normalized = (requested or "ext").strip().lower() or "ext"
    if normalized == "default":
        configured_default = config.get("protocols", {}).get("default", "ext")
        if isinstance(configured_default, str) and configured_default.strip():
            normalized = configured_default.strip().lower()
        else:
            normalized = "ext"

    if normalized not in SUPPORTED_PROTOCOLS:
        available = ", ".join(sorted(SUPPORTED_PROTOCOLS))
        raise ValueError(f"Unknown protocol: {requested!r}. Available: {available}")

    return normalized


def is_censo_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is a CENSO-family protocol."""
    return protocol_spec.name in CENSO_PROTOCOLS


def is_ext_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol uses the ext conformer-search pipeline."""
    return protocol_spec.name in {"ext", "allopt"}


def is_full_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is a full refinement pipeline."""
    return protocol_spec.name in {"censo-full", "censo-full-safe"}


def is_lite_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is a lite refinement pipeline."""
    return protocol_spec.name in {"censo-lite"}


def is_zero_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is a zero-cost pipeline."""
    return protocol_spec.name in {"censo-zero"}


def is_benchmark_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is a reference-level benchmark SP pipeline."""
    return protocol_spec.name in {"reference-sp"}


def resolve_protocol_spec(config: Dict[str, Any], protocol: str) -> ProtocolSpec:
    """
    Resolve protocol specification from configuration.
    
    Lookup order:
      1. config['protocols'][protocol] (primary path)
      2. _get_default_protocol_config(protocol) (fallback)
    
    Engine resolution (3-level fallback):
      1. protocol-level: proto_cfg['opt_engine'] / proto_cfg['freq_engine']
      2. theory-level:   config['theory']['optimization']['engine'] / config['theory']['frequency']['engine']
      3. hardcoded:      "gaussian"
    
    Args:
        config: Full configuration dictionary
        protocol: Protocol name
        
    Returns:
        ProtocolSpec instance
    """
    protocol = resolve_protocol_name(config, protocol)

    proto_cfg = config.get('protocols', {}).get(protocol, {})
    if not proto_cfg:
        fallback_name = PROTOCOL_CONFIG_FALLBACKS.get(protocol)
        if fallback_name is not None:
            proto_cfg = config.get('protocols', {}).get(fallback_name, {})

    if not isinstance(proto_cfg, dict) or not proto_cfg:
        proto_cfg = _get_default_protocol_config(protocol)
    
    two_stage = proto_cfg.get('two_stage_enabled', True)
    ngeom_default = proto_cfg.get('ngeom_default', 3)
    ngeom_max = proto_cfg.get('ngeom_max', 6)
    
    funnel_cfg = proto_cfg.get('funnel') or {}
    funnel_policy = FunnelPolicy(
        search_mode=funnel_cfg.get('search_mode', 'crest_gfn2'),
        clustering_mode=funnel_cfg.get('clustering_mode', 'isostat'),
        prescreen_mode=funnel_cfg.get('prescreen_mode', 'none'),
        rerank_mode=funnel_cfg.get('rerank_mode', 'none'),
        use_mrrho_like_correction=funnel_cfg.get('use_mrrho_like_correction', False),
        survivor_window_kcal=funnel_cfg.get('survivor_window_kcal', 3.0),
        prescreen_window_kcal=funnel_cfg.get('prescreen_window_kcal', 4.0),
        screening_window_kcal=funnel_cfg.get('screening_window_kcal', 3.5),
        optimize_limit=funnel_cfg.get('optimize_limit'),
        top2_fallback_enabled=funnel_cfg.get('top2_fallback_enabled', False),
        boltzmann_cutoff=funnel_cfg.get('boltzmann_cutoff', 0.90),
    )
    
    handoff_cfg = proto_cfg.get('handoff') or {}
    handoff_policy = HandoffPolicy(
        mode=handoff_cfg.get('mode', 'optimize_rank1'),
        fallback_mode=handoff_cfg.get('fallback_mode'),
        small_gap_kcal=handoff_cfg.get('small_gap_kcal', 1.0),
        ranking_after_handoff=handoff_cfg.get('ranking_after_handoff', 'final_sp_minimum'),
    )
    
    final_cfg = proto_cfg.get('final_opt_sp') or {}

    stages_cfg = proto_cfg.get('stages') or {}
    enable_crest = stages_cfg.get('crest', True)
    enable_clustering = stages_cfg.get('clustering', True)
    enable_optimization = stages_cfg.get('optimization', True)
    enable_frequency = stages_cfg.get('frequency', True)
    enable_single_point = stages_cfg.get('single_point', True)
    enable_shermo = stages_cfg.get('shermo', True)
    
    # 3-level engine fallback: protocol → theory → "gaussian"
    opt_engine = proto_cfg.get('opt_engine')
    if opt_engine is None:
        opt_engine = config.get('theory', {}).get('optimization', {}).get('engine')
    if opt_engine is None:
        opt_engine = "gaussian"

    freq_engine = proto_cfg.get('freq_engine')
    if freq_engine is None:
        freq_engine = config.get('theory', {}).get('frequency', {}).get('engine')
    if freq_engine is None:
        freq_engine = "gaussian"

    return ProtocolSpec(
        name=protocol,
        two_stage_enabled=two_stage,
        ngeom_default=ngeom_default,
        ngeom_max=ngeom_max,
        funnel_policy=funnel_policy,
        handoff_policy=handoff_policy,
        final_sp_method=final_cfg.get('final_sp_method') or 'wB97X-D4',
        final_sp_basis=final_cfg.get('final_sp_basis') or 'def2-TZVPP',
        opt_engine=opt_engine,
        freq_engine=freq_engine,
        enable_crest=enable_crest,
        enable_clustering=enable_clustering,
        enable_optimization=enable_optimization,
        enable_frequency=enable_frequency,
        enable_single_point=enable_single_point,
        enable_shermo=enable_shermo,
    )


def _get_default_protocol_config(protocol: str) -> Dict[str, Any]:
    """Get default configuration for a protocol."""
    defaults = {
        'ext': {
            'two_stage_enabled': True,
            'ngeom_default': 3,
            'ngeom_max': 6,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_two_stage_gfn0_to_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'none',
                'rerank_mode': 'none',
            },
            'handoff': {
                'mode': 'optimize_all_candidates',
                'ranking_after_handoff': 'final_sp_minimum',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': True,
                'frequency': True,
                'single_point': True,
                'shermo': True,
            },
        },
        'censo-zero': {
            'two_stage_enabled': False,
            'ngeom_default': 1,
            'ngeom_max': 3,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'none',
                'rerank_mode': 'none',
                'survivor_window_kcal': 1.0,
            },
            'handoff': {
                'mode': 'optimize_rank1',
                'ranking_after_handoff': 'xtb_energy',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': False,
                'frequency': False,
                'single_point': True,
                'shermo': False,
            },
        },
        'censo-lite': {
            'two_stage_enabled': False,
            'ngeom_default': 4,
            'ngeom_max': 6,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'none',
                'rerank_mode': 'r2scan3c_sp',
                'use_mrrho_like_correction': True,
                'optimize_limit': 1,
                'top2_fallback_enabled': True,
                'boltzmann_cutoff': 0.90,
            },
            'handoff': {
                'mode': 'optimize_rank1',
                'fallback_mode': 'optimize_top2_if_gap_small',
                'small_gap_kcal': 1.0,
                'ranking_after_handoff': 'final_sp_minimum',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': False,
                'frequency': False,
                'single_point': True,
                'shermo': False,
            },
        },
        'censo-full': {
            'two_stage_enabled': False,
            'ngeom_default': 12,
            'ngeom_max': 24,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'low_cost_dft_sp',
                'rerank_mode': 'r2scan3c_sp_plus_mrrho',
                'prescreen_window_kcal': 4.0,
                'screening_window_kcal': 3.5,
                'survivor_window_kcal': 3.0,
            },
            'handoff': {
                'mode': 'optimize_all_survivors_within_window',
                'ranking_after_handoff': 'final_sp_plus_boltzmann',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': True,
                'frequency': True,
                'single_point': True,
                'shermo': True,
            },
        },
        'censo-full-safe': {
            'two_stage_enabled': False,
            'ngeom_default': 12,
            'ngeom_max': 24,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'low_cost_dft_sp',
                'rerank_mode': 'r2scan3c_sp_plus_mrrho',
                'prescreen_window_kcal': 8.0,
                'screening_window_kcal': 6.0,
                'survivor_window_kcal': 4.0,
                'boltzmann_cutoff': 0.99,
            },
            'handoff': {
                'mode': 'optimize_all_survivors_within_window',
                'ranking_after_handoff': 'final_sp_plus_boltzmann',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': True,
                'frequency': True,
                'single_point': True,
                'shermo': True,
            },
        },
        'allopt': {
            'two_stage_enabled': True,
            'ngeom_default': 48,
            'ngeom_max': 48,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'crest_two_stage_gfn0_to_gfn2',
                'clustering_mode': 'isostat',
                'prescreen_mode': 'none',
                'rerank_mode': 'none',
            },
            'handoff': {
                'mode': 'optimize_all_candidates',
                'ranking_after_handoff': 'final_sp_plus_boltzmann',
            },
            'final_opt_sp': {
                'final_sp_method': 'wB97X-D4',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': True,
                'clustering': True,
                'optimization': True,
                'frequency': True,
                'single_point': True,
                'shermo': True,
            },
        },
        'reference-sp': {
            'two_stage_enabled': False,
            'ngeom_default': 48,
            'ngeom_max': 48,
            'opt_engine': None,
            'freq_engine': None,
            'funnel': {
                'search_mode': 'external_xyz',
                'clustering_mode': 'none',
                'prescreen_mode': 'none',
                'rerank_mode': 'none',
            },
            'handoff': {
                'mode': 'optimize_all_candidates',
                'ranking_after_handoff': 'final_sp_minimum',
            },
            'final_opt_sp': {
                'final_sp_method': 'DLPNO-CCSD(T)',
                'final_sp_basis': 'def2-TZVPP',
            },
            'stages': {
                'crest': False,
                'clustering': False,
                'optimization': False,
                'frequency': False,
                'single_point': True,
                'shermo': False,
            },
        },
    }

    alias_defaults = {
        'default': 'ext',
    }

    resolved_protocol = alias_defaults.get(protocol, protocol)
    if resolved_protocol not in defaults:
        available = ", ".join(sorted(SUPPORTED_PROTOCOLS))
        raise ValueError(f"Unknown protocol: {protocol!r}. Available: {available}")

    return deepcopy(defaults[resolved_protocol])


__all__ = [
    'FunnelPolicy',
    'HandoffPolicy',
    'ProtocolSpec',
    'SUPPORTED_PROTOCOLS',
    'CENSO_PROTOCOLS',
    'resolve_protocol_name',
    'resolve_protocol_spec',
    'is_censo_protocol',
    'is_ext_protocol',
    'is_full_protocol',
    'is_lite_protocol',
    'is_zero_protocol',
    'is_benchmark_protocol',
]
