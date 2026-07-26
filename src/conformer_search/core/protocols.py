"""
Protocols
=========

Conformer search protocol definitions.

Author: QCcalc Team (adapted from RPH)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FunnelPolicy:
    """Funnel search policy parameters."""

    search_mode: str = "crest_gfn2"
    clustering_mode: str = "isostat"
    prescreen_mode: str = "none"
    rerank_mode: str = "none"
    use_mrrho_like_correction: bool = False
    survivor_window_kcal: float | None = 3.0
    prescreen_window_kcal: float | None = 4.0
    screening_window_kcal: float | None = 3.5
    narrow_window_kcal: float | None = None
    optimize_limit: int | None = None
    top2_fallback_enabled: bool = False
    boltzmann_cutoff: float | None = 0.90


@dataclass(frozen=True)
class HandoffPolicy:
    """Handoff policy parameters."""

    mode: str = "optimize_rank1"
    fallback_mode: str | None = None
    small_gap_kcal: float | None = 1.0
    ranking_after_handoff: str = "final_sp_minimum"


@dataclass(frozen=True)
class ProtocolSpec:
    """
    Complete protocol specification.

    Attributes:
        name: Protocol name (ext, full, lite, zero)
        two_stage_enabled: Enable two-stage CREST search
        ngeom_default: Default number of geometries to optimize
        ngeom_max: Maximum number of geometries
        funnel_policy: Funnel search parameters
        handoff_policy: DFT handoff parameters
        final_sp_method: Final SP method
        final_sp_basis: Final SP basis set
        opt_engine: Optimization engine (deprecated; ORCA is the only engine)
        freq_engine: Frequency engine (deprecated; ORCA is the only engine)
        enable_crest: Enable CREST conformer search stage
        enable_clustering: Enable ISOSTAT clustering stage
        enable_optimization: Enable DFT geometry optimization stage
        enable_frequency: Enable frequency calculation stage
        enable_single_point: Enable high-precision single-point energy stage
        enable_shermo: Enable Shermo thermodynamic correction stage
        sp_engine: Single-point engine (deprecated; ORCA is the only engine)
    """

    name: str
    two_stage_enabled: bool = True
    ngeom_default: int = 3
    ngeom_max: int = 6
    funnel_policy: FunnelPolicy = field(default_factory=FunnelPolicy)
    handoff_policy: HandoffPolicy = field(default_factory=HandoffPolicy)
    mrrho_settings: dict[str, Any] = field(default_factory=dict)
    final_sp_method: str = "wB97M-V"
    final_sp_basis: str = "def2-TZVPP"
    opt_engine: str = "orca"
    freq_engine: str = "orca"
    sp_engine: str = "orca"
    opt_method: str = "r2SCAN-3c"
    opt_basis: str = ""
    # Hessian policy for opt stage: "auto" / 0 / N / None (None=follow config).
    # Mirrors the public recalc_hess semantics (plan §5.1).
    opt_recalc_hess: object = None
    opt_solvent: str | None = None
    opt_solvent_model: str | None = None
    sp_solvent: str | None = None
    sp_solvent_model: str | None = None
    enable_crest: bool = True
    enable_clustering: bool = True
    enable_optimization: bool = True
    enable_frequency: bool = True
    enable_single_point: bool = True
    enable_shermo: bool = True
    crest_energy_window_kcal: float | None = None
    stage2_energy_window_kcal: float | None = None


SUPPORTED_PROTOCOLS = {"ext", "default", "full", "lite", "zero", "benchmark"}


def is_ext_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is ext or default."""
    return protocol_spec.name in ("ext", "default")


def is_full_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is full."""
    return protocol_spec.name == "full"


def is_lite_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is lite."""
    return protocol_spec.name == "lite"


def is_zero_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is zero."""
    return protocol_spec.name == "zero"


def is_benchmark_protocol(protocol_spec: ProtocolSpec) -> bool:
    """Check if protocol is benchmark."""
    return protocol_spec.name == "benchmark"


def resolve_protocol_spec(
    config: dict[str, Any], protocol: str, levels: dict[str, Any] | None = None
) -> ProtocolSpec:
    """
    Resolve protocol specification from configuration.

    Resolution hierarchy:
      1. ``levels`` (explicit per-job overrides) — highest priority
      2. User config ``config['protocols'][protocol]``
      3. Built-in protocol defaults (:func:`_get_default_protocol_config`)
      4. Hardcoded fallbacks

    For method/basis (the core protocol identity), ``config['theory']`` is
    intentionally **not** consulted as a fallback. This prevents a global user
    config from silently changing what ``lite`` / ``full`` / ``zero`` mean.
    Solvent, solvent model, and engine still fall back to ``config['theory']``
    when the protocol does not specify them, so users can customize the solvent
    without having to redefine the whole protocol.

    Args:
        config: Full configuration dictionary.
        protocol: Protocol name.
        levels: Optional explicit per-job method overrides, e.g.
            ``{"optimization": {"method": "B3LYP", "solvent": "methanol"}}``.

    Returns:
        ProtocolSpec instance.
    """
    protocol = protocol.lower().strip()
    if protocol not in SUPPORTED_PROTOCOLS:
        protocol = "ext"

    # Normalise levels to a dict of dicts.
    levels = levels or {}
    if not isinstance(levels, dict):
        levels = {}
    levels = {str(k): (v if isinstance(v, dict) else {}) for k, v in levels.items()}

    proto_cfg = config.get("protocols", {}).get(protocol, {})
    default_proto_cfg = _get_default_protocol_config(protocol)

    if not proto_cfg:
        proto_cfg = default_proto_cfg

    two_stage = proto_cfg.get("two_stage_enabled", default_proto_cfg.get("two_stage_enabled", True))
    ngeom_default = proto_cfg.get("ngeom_default", default_proto_cfg.get("ngeom_default", 3))
    ngeom_max = proto_cfg.get("ngeom_max", default_proto_cfg.get("ngeom_max", 6))
    crest_energy_window_kcal = proto_cfg.get("crest_energy_window_kcal")
    stage2_energy_window_kcal = proto_cfg.get("stage2_energy_window_kcal")

    funnel_cfg = proto_cfg.get("funnel") or default_proto_cfg.get("funnel") or {}
    funnel_policy = FunnelPolicy(
        search_mode=funnel_cfg.get("search_mode", "crest_gfn2"),
        clustering_mode=funnel_cfg.get("clustering_mode", "isostat"),
        prescreen_mode=funnel_cfg.get("prescreen_mode", "none"),
        rerank_mode=funnel_cfg.get("rerank_mode", "none"),
        use_mrrho_like_correction=funnel_cfg.get("use_mrrho_like_correction", False),
        survivor_window_kcal=funnel_cfg.get("survivor_window_kcal", 3.0),
        prescreen_window_kcal=funnel_cfg.get("prescreen_window_kcal", 4.0),
        screening_window_kcal=funnel_cfg.get("screening_window_kcal", 3.5),
        narrow_window_kcal=funnel_cfg.get("narrow_window_kcal"),
        optimize_limit=funnel_cfg.get("optimize_limit"),
        top2_fallback_enabled=funnel_cfg.get("top2_fallback_enabled", False),
        boltzmann_cutoff=funnel_cfg.get("boltzmann_cutoff", 0.90),
    )

    handoff_cfg = proto_cfg.get("handoff") or default_proto_cfg.get("handoff") or {}
    handoff_policy = HandoffPolicy(
        mode=handoff_cfg.get("mode", "optimize_rank1"),
        fallback_mode=handoff_cfg.get("fallback_mode"),
        small_gap_kcal=handoff_cfg.get("small_gap_kcal", 1.0),
        ranking_after_handoff=handoff_cfg.get("ranking_after_handoff", "final_sp_minimum"),
    )

    # Read mrrho_settings: protocol-level takes priority, fall back to top-level config
    mrrho_settings = proto_cfg.get("mrrho_settings", {})
    if not mrrho_settings:
        mrrho_settings = config.get("mrrho_settings", {})

    final_cfg = proto_cfg.get("final_opt_sp") or default_proto_cfg.get("final_opt_sp") or {}

    stages_cfg = proto_cfg.get("stages") or default_proto_cfg.get("stages") or {}
    enable_crest = stages_cfg.get("crest", True)
    enable_clustering = stages_cfg.get("clustering", True)
    enable_optimization = stages_cfg.get("optimization", True)
    enable_frequency = stages_cfg.get("frequency", True)
    enable_single_point = stages_cfg.get("single_point", True)
    enable_shermo = stages_cfg.get("shermo", True)

    theory_opt = config.get("theory", {}).get("optimization", {})
    theory_sp = config.get("theory", {}).get("single_point", {})
    theory_freq = config.get("theory", {}).get("frequency", {})

    opt_level = levels.get("optimization", {})
    freq_level = levels.get("frequency", {})
    sp_level = levels.get("single_point", {})

    # Engines: levels 'engine' > protocol 'opt_engine' > default protocol
    # > theory 'engine' > hardcoded
    opt_engine = opt_level.get("engine")
    if opt_engine is None:
        opt_engine = proto_cfg.get("opt_engine")
    if opt_engine is None:
        opt_engine = default_proto_cfg.get("opt_engine")
    if opt_engine is None:
        opt_engine = theory_opt.get("engine")
    if opt_engine is None:
        opt_engine = "orca"

    freq_engine = freq_level.get("engine")
    if freq_engine is None:
        freq_engine = proto_cfg.get("freq_engine")
    if freq_engine is None:
        freq_engine = theory_freq.get("engine")
    if freq_engine is None:
        freq_engine = opt_engine

    sp_engine = sp_level.get("engine")
    if sp_engine is None:
        sp_engine = proto_cfg.get("sp_engine")
    if sp_engine is None:
        sp_engine = default_proto_cfg.get("sp_engine")
    if sp_engine is None:
        sp_engine = theory_sp.get("engine")
    if sp_engine is None:
        sp_engine = "orca"

    # Methods / basis: levels (method/basis) > protocol defaults > hardcoded.
    # config['theory'] is NOT used for methods/basis.
    opt_method = opt_level.get("method")
    if opt_method is None:
        opt_method = proto_cfg.get("opt_method")
    if opt_method is None:
        opt_method = default_proto_cfg.get("opt_method")
    if opt_method is None:
        opt_method = "r2SCAN-3c"

    opt_basis = opt_level.get("basis")
    if opt_basis is None:
        opt_basis = proto_cfg.get("opt_basis")
    if opt_basis is None:
        opt_basis = default_proto_cfg.get("opt_basis")
    if opt_basis is None:
        opt_basis = ""

    # Hessian policy (plan §10.3): read from frontend ``levels`` (already
    # converted to ``optimization.recalc_hess`` by
    # ``convert_method_levels_to_protocol_levels``). Normalise through the
    # shared helper so invalid values raise here rather than silently
    # reaching ORCA. ``None`` ⇒ follow config (default).
    opt_recalc_hess_raw = opt_level.get("recalc_hess")
    opt_recalc_hess: object = None
    if opt_recalc_hess_raw is not None:
        from acp.chem.composition import normalize_recalc_hess as _normalize

        opt_recalc_hess = _normalize(opt_recalc_hess_raw)

    # Solvents: levels 'solvent' > protocol 'opt_solvent' > theory 'solvent' > None
    opt_solvent = opt_level.get("solvent")
    if opt_solvent is None:
        opt_solvent = proto_cfg.get("opt_solvent")
    if opt_solvent is None:
        opt_solvent = theory_opt.get("solvent")

    opt_solvent_model = opt_level.get("solvent_model")
    if opt_solvent_model is None:
        opt_solvent_model = proto_cfg.get("opt_solvent_model")
    if opt_solvent_model is None:
        opt_solvent_model = theory_opt.get("solvent_model")
    if opt_solvent_model is None:
        opt_solvent_model = "none"

    sp_solvent = sp_level.get("solvent")
    if sp_solvent is None:
        sp_solvent = proto_cfg.get("sp_solvent")
    if sp_solvent is None:
        sp_solvent = theory_sp.get("solvent")

    sp_solvent_model = sp_level.get("solvent_model")
    if sp_solvent_model is None:
        sp_solvent_model = proto_cfg.get("sp_solvent_model")
    if sp_solvent_model is None:
        sp_solvent_model = theory_sp.get("solvent_model")
    if sp_solvent_model is None:
        sp_solvent_model = "none"

    # Final SP method/basis: levels (method/basis) > protocol > default protocol > hardcoded
    final_sp_method = sp_level.get("method")
    if final_sp_method is None:
        final_sp_method = final_cfg.get("final_sp_method")
    if final_sp_method is None:
        final_sp_method = default_proto_cfg.get("final_opt_sp", {}).get("final_sp_method")
    if final_sp_method is None:
        final_sp_method = "wB97M-V"

    final_sp_basis = sp_level.get("basis")
    if final_sp_basis is None:
        final_sp_basis = final_cfg.get("final_sp_basis")
    if final_sp_basis is None:
        final_sp_basis = default_proto_cfg.get("final_opt_sp", {}).get("final_sp_basis")
    if final_sp_basis is None:
        final_sp_basis = "def2-TZVPP"

    return ProtocolSpec(
        name=protocol,
        two_stage_enabled=two_stage,
        ngeom_default=ngeom_default,
        ngeom_max=ngeom_max,
        funnel_policy=funnel_policy,
        handoff_policy=handoff_policy,
        mrrho_settings=mrrho_settings,
        final_sp_method=final_sp_method,
        final_sp_basis=final_sp_basis,
        opt_engine=opt_engine,
        freq_engine=freq_engine,
        sp_engine=sp_engine,
        opt_method=opt_method,
        opt_basis=opt_basis,
        opt_recalc_hess=opt_recalc_hess,
        opt_solvent=opt_solvent,
        opt_solvent_model=opt_solvent_model,
        sp_solvent=sp_solvent,
        sp_solvent_model=sp_solvent_model,
        enable_crest=enable_crest,
        enable_clustering=enable_clustering,
        enable_optimization=enable_optimization,
        enable_frequency=enable_frequency,
        enable_single_point=enable_single_point,
        enable_shermo=enable_shermo,
        crest_energy_window_kcal=crest_energy_window_kcal,
        stage2_energy_window_kcal=stage2_energy_window_kcal,
    )


def get_protocol_expected_methods(protocol: str) -> dict[str, dict[str, str | None]]:
    """Return the canonical method/basis/engine for each stage of *protocol*.

    These are the values used when no user config or ``levels`` override is
    supplied. They are used by submission-time validation.
    """
    protocol = protocol.lower().strip()
    if protocol not in SUPPORTED_PROTOCOLS:
        protocol = "ext"
    cfg = _get_default_protocol_config(protocol)
    return {
        "optimization": {
            "method": cfg.get("opt_method"),
            "basis": cfg.get("opt_basis"),
            "engine": cfg.get("opt_engine"),
        },
        "single_point": {
            "method": cfg.get("final_opt_sp", {}).get("final_sp_method"),
            "basis": cfg.get("final_opt_sp", {}).get("final_sp_basis"),
            "engine": cfg.get("sp_engine"),
        },
        "frequency": {
            "engine": cfg.get("freq_engine"),
        },
    }


def validate_protocol_methods(
    config: dict[str, Any],
    protocol: str,
    levels: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Validate that resolved methods match the protocol's canonical defaults.

    Custom methods are allowed only when provided explicitly through
    ``levels``.  Solvent and engine deviations are not considered errors.

    Returns:
        ``(is_valid, list_of_error_messages)``.
    """
    spec = resolve_protocol_spec(config, protocol, levels=levels)
    expected = get_protocol_expected_methods(protocol)
    errors: list[str] = []

    levels = levels or {}

    def _level_override(stage: str, key: str) -> bool:
        return levels.get(stage, {}).get(key) is not None

    opt = expected.get("optimization", {})
    if opt.get("method") is not None and spec.opt_method != opt["method"]:
        if not _level_override("optimization", "method"):
            errors.append(
                f"protocol {protocol}: optimization method is '{spec.opt_method}' "
                f"but the canonical default is '{opt['method']}'; "
                "provide levels.optimization.method to override"
            )
    if opt.get("basis") is not None and spec.opt_basis != opt["basis"]:
        if not _level_override("optimization", "basis"):
            errors.append(
                f"protocol {protocol}: optimization basis is '{spec.opt_basis}' "
                f"but the canonical default is '{opt['basis']}'; "
                "provide levels.optimization.basis to override"
            )

    sp = expected.get("single_point", {})
    if sp.get("method") is not None and spec.final_sp_method != sp["method"]:
        if not _level_override("single_point", "method"):
            errors.append(
                f"protocol {protocol}: single_point method is '{spec.final_sp_method}' "
                f"but the canonical default is '{sp['method']}'; "
                "provide levels.single_point.method to override"
            )
    if sp.get("basis") is not None and spec.final_sp_basis != sp["basis"]:
        if not _level_override("single_point", "basis"):
            errors.append(
                f"protocol {protocol}: single_point basis is '{spec.final_sp_basis}' "
                f"but the canonical default is '{sp['basis']}'; "
                "provide levels.single_point.basis to override"
            )

    return (not errors, errors)


def _get_default_protocol_config(protocol: str) -> dict[str, Any]:
    """Get default configuration for a protocol."""
    defaults = {
        "ext": {
            "two_stage_enabled": True,
            "ngeom_default": 3,
            "ngeom_max": 6,
            "stage2_energy_window_kcal": 3.0,
            "opt_engine": None,
            "freq_engine": None,
            "sp_engine": None,
            "funnel": {
                "search_mode": "crest_two_stage_gfn0_to_gfn2",
                "clustering_mode": "isostat",
                "prescreen_mode": "none",
                "rerank_mode": "none",
            },
            "handoff": {
                "mode": "optimize_all_candidates",
                "ranking_after_handoff": "final_sp_minimum",
            },
            "final_opt_sp": {
                "final_sp_method": "wB97M-V",
                "final_sp_basis": "def2-TZVPP",
            },
            "stages": {
                "crest": True,
                "clustering": True,
                "optimization": True,
                "frequency": True,
                "single_point": True,
                "shermo": True,
            },
        },
        "benchmark": {
            "two_stage_enabled": True,
            "ngeom_default": 3,
            "ngeom_max": 6,
            "stage2_energy_window_kcal": 3.0,
            "opt_engine": None,
            "freq_engine": None,
            "sp_engine": None,
            "funnel": {
                "search_mode": "crest_two_stage_gfn0_to_gfn2",
                "clustering_mode": "isostat",
                "prescreen_mode": "none",
                "rerank_mode": "none",
            },
            "handoff": {
                "mode": "optimize_all_candidates",
                "ranking_after_handoff": "final_sp_minimum",
            },
            "final_opt_sp": {
                "final_sp_method": "DLPNO-CCSD(T)",
                "final_sp_basis": "def2-TZVPP",
            },
            "stages": {
                "crest": True,
                "clustering": True,
                "optimization": True,
                "frequency": True,
                "single_point": True,
                "shermo": True,
            },
        },
        "full": {
            "two_stage_enabled": False,
            "ngeom_default": 12,
            "ngeom_max": 24,
            "opt_engine": None,
            "freq_engine": None,
            "sp_engine": None,
            "funnel": {
                "search_mode": "crest_gfn2",
                "clustering_mode": "isostat",
                "prescreen_mode": "low_cost_dft_sp",
                "rerank_mode": "r2scan3c_sp_plus_mrrho",
                "use_mrrho_like_correction": True,
                "prescreen_window_kcal": 4.0,
                "screening_window_kcal": 3.5,
                "survivor_window_kcal": 3.0,
            },
            "handoff": {
                "mode": "optimize_all_survivors_within_window",
                "ranking_after_handoff": "final_sp_plus_boltzmann",
            },
            "final_opt_sp": {
                "final_sp_method": "wB97M-V",
                "final_sp_basis": "def2-TZVPP",
            },
            "stages": {
                "crest": True,
                "clustering": True,
                "optimization": True,
                "frequency": True,
                "single_point": True,
                "shermo": True,
            },
        },
        "lite": {
            "two_stage_enabled": False,
            "ngeom_default": 4,
            "ngeom_max": 6,
            "crest_energy_window_kcal": 3.0,
            "opt_engine": "orca",
            "freq_engine": "orca",
            "sp_engine": "orca",
            "opt_method": "r2SCAN-3c",
            "opt_basis": "",
            "funnel": {
                "search_mode": "crest_gfn2",
                "clustering_mode": "isostat",
                "prescreen_mode": "none",
                "rerank_mode": "r2scan3c_sp",
                "use_mrrho_like_correction": True,
                "optimize_limit": 1,
                "top2_fallback_enabled": True,
                "boltzmann_cutoff": 0.90,
            },
            "handoff": {
                "mode": "optimize_rank1",
                "fallback_mode": "optimize_top2_if_gap_small",
                "small_gap_kcal": 1.0,
                "ranking_after_handoff": "final_sp_minimum",
            },
            "final_opt_sp": {
                "final_sp_method": "wB97M-V",
                "final_sp_basis": "def2-TZVPP",
            },
            "stages": {
                "crest": True,
                "clustering": True,
                "optimization": True,
                "frequency": True,
                "single_point": True,
                "shermo": True,
            },
        },
        "zero": {
            "two_stage_enabled": False,
            "ngeom_default": 1,
            "ngeom_max": 3,
            "opt_engine": None,
            "freq_engine": None,
            "sp_engine": None,
            "opt_method": "r2SCAN-3c",
            "opt_basis": "",
            "funnel": {
                "search_mode": "crest_gfn2",
                "clustering_mode": "minimal",
                "prescreen_mode": "none",
                "rerank_mode": "none",
                "use_mrrho_like_correction": False,
                "narrow_window_kcal": 0.5,
                "optimize_limit": 1,
            },
            "handoff": {
                "mode": "optimize_rank1",
                "fallback_mode": "optimize_all_within_0p5_kcal",
                "small_gap_kcal": 0.5,
                "ranking_after_handoff": "final_sp_minimum",
            },
            "final_opt_sp": {},
            "stages": {
                "crest": True,
                "clustering": True,
                "optimization": True,
                "frequency": True,
                "single_point": True,
                "shermo": True,
            },
            "mrrho_settings": {
                "gfn_level": 2,
                "sthr": 50.0,
                "imagthr": -100.0,
                "temperature_k": 298.15,
            },
        },
    }

    return defaults.get(protocol, defaults["ext"])
