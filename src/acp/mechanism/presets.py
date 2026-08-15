"""Mechanism path-strategy and fidelity presets.

Orthogonal preset axes (mirroring RPH v4.0.1's FidelityProfile design):

* **path strategy** — *how* the reaction path is searched (guided-scan /
  rph-reverse / direct-ts / endpoint-path);
* **fidelity** — *at what level* stationary points are refined (s3 / s4).

A mechanism job selects ``strategy × fidelity``; the workflow resolves both
into concrete per-stage parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from acp.mechanism.models import Fidelity, PathStrategy

RPH_CENSO_LITE_MODE = "rph-censo-lite"
XTB_FAST_MODE = "xtb-fast"

RPH_PROFILE_IDS: dict[Fidelity, str] = {
    "s3": "b97_3c_r2scan_3c_v1",
    "s4": "m062x_wb97mv_v1",
}

_SCF_CONVERGENCE_KEYWORD: dict[str, str] = {
    "loose": "LooseSCF",
    "normal": "NormalSCF",
    "tight": "TightSCF",
    "verytight": "VeryTightSCF",
}


def _scf_keyword(value: object) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().lower()
    return _SCF_CONVERGENCE_KEYWORD.get(text, str(value).strip())


def _optional_str(value: object) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value)


def _profile_level(levels: dict[str, Any], level_id: str) -> dict[str, Any]:
    level = levels.get(level_id)
    return level if isinstance(level, dict) else {}


def _build_fidelity_profiles() -> dict[Fidelity, FidelityProfile]:
    """Build the s3/s4 execution profiles from the authoritative catalog.

    ``acp.catalog.METHOD_SCHEMAS["mechanism"]["profiles"]`` is the single
    source of truth for the RPH preset contract; this module adapts its
    per-stage level dicts into :class:`FidelityProfile` objects for execution.
    The first profile per fidelity wins (the canonical ``rph-sX`` entry).
    """
    from acp.catalog import METHOD_SCHEMAS

    profiles = METHOD_SCHEMAS.get("mechanism", {}).get("profiles")
    built: dict[Fidelity, FidelityProfile] = {}
    for profile in profiles if isinstance(profiles, list) else []:
        if not isinstance(profile, dict):
            continue
        levels = profile.get("levels")
        if not isinstance(levels, dict):
            continue
        scan = _profile_level(levels, "scan")
        fidelity = scan.get("fidelity")
        if fidelity not in {"s3", "s4"} or fidelity in built:
            continue
        ts_opt = _profile_level(levels, "ts_opt")
        freq = _profile_level(levels, "freq")
        sp = _profile_level(levels, "sp")
        irc = _profile_level(levels, "irc")
        scan_points = irc_points = None
        try:
            scan_points = (
                int(scan.get("scan_points")) if scan.get("scan_points") is not None else None
            )
            irc_points = int(irc.get("irc_points")) if irc.get("irc_points") is not None else None
        except (TypeError, ValueError):
            pass
        built[cast(Fidelity, fidelity)] = FidelityProfile(
            name=cast(Fidelity, fidelity),
            scan_points=scan_points if scan_points is not None else 21,
            ts_method=_optional_str(ts_opt.get("functional")) or "B97-3c",
            ts_basis=_optional_str(ts_opt.get("basis")) or "",
            ts_grid=_optional_str(ts_opt.get("grid")),
            ts_scf=_scf_keyword(ts_opt.get("scf_convergence")),
            freq_method=_optional_str(freq.get("functional")) or "B97-3c",
            freq_basis=_optional_str(freq.get("basis")) or "",
            sp_method=_optional_str(sp.get("functional")) or "r2SCAN-3c",
            sp_basis=_optional_str(sp.get("basis")) or "",
            sp_ri_approximation=_optional_str(sp.get("ri_approximation")),
            sp_aux_j=_optional_str(sp.get("aux_j_basis")),
            solvent=_optional_str(sp.get("solvent")),
            solvent_model=_optional_str(sp.get("solvent_model")) or "none",
            ts_initial_hessian=_optional_str(ts_opt.get("ts_initial_hessian")) or "calculate",
            irc_points=irc_points if irc_points is not None else 30,
        )
    return built


@dataclass(frozen=True)
class FidelityProfile:
    """Per-stage refinement parameters for one fidelity level (s3 / s4).

    Attributes:
        name: Fidelity id (``"s3"`` / ``"s4"``).
        scan_points: Default relaxed-scan frame count.
        ts_method / ts_basis: OptTS method/basis (None basis = composite 3c).
        ts_grid / ts_scf: Optional OptTS grid / SCF convergence keywords.
        freq_method / freq_basis: Independent frequency level.
        sp_method / sp_basis: Final single-point level.
        solvent / solvent_model: Workflow-global solvation.
        ts_initial_hessian: ``"calculate"`` / ``"model"`` / ``"read"``.
        ts_recalc_hess: Recalculate Hessian interval.
        ts_trust_radius: Initial TrustRadius.
        irc_points: Default IRC MaxIter.
    """

    name: str
    scan_points: int = 21
    ts_method: str = "B97-3c"
    ts_basis: str = ""
    ts_grid: str | None = None
    ts_scf: str | None = None
    freq_method: str = "B97-3c"
    freq_basis: str = ""
    sp_method: str = "r2SCAN-3c"
    sp_basis: str = ""
    sp_ri_approximation: str | None = None
    sp_aux_j: str | None = None
    solvent: str | None = None
    solvent_model: str = "none"
    ts_initial_hessian: str = "calculate"
    ts_recalc_hess: int = 5
    ts_trust_radius: float = 0.15
    irc_points: int = 30

    def ts_kwargs(self) -> dict[str, object]:
        """Keyword dict for the ORCA backend transition-state call."""
        kwargs: dict[str, object] = {
            "method": self.ts_method,
            "basis": self.ts_basis,
            "initial_hessian": self.ts_initial_hessian,
            "recalc_hess": self.ts_recalc_hess,
            "trust_radius": self.ts_trust_radius,
            "solvent": self.solvent,
            "solvent_model": self.solvent_model,
        }
        if self.ts_grid:
            kwargs["grid"] = self.ts_grid
        if self.ts_scf:
            kwargs["scf"] = self.ts_scf
        return kwargs

    def sp_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "method": self.sp_method,
            "basis": self.sp_basis,
            "solvent": self.solvent,
            "solvent_model": self.solvent_model,
        }
        if self.sp_ri_approximation:
            kwargs["ri_approximation"] = self.sp_ri_approximation
        if self.sp_aux_j:
            kwargs["aux_j_basis"] = self.sp_aux_j
        return kwargs


FIDELITY_PROFILES: dict[Fidelity, FidelityProfile] = _build_fidelity_profiles()


@dataclass(frozen=True)
class PathStrategySpec:
    """Description of one path-search strategy.

    Attributes:
        name: Strategy id (``guided-scan`` / ``rph-reverse`` / ``direct-ts`` /
            ``endpoint-path``).
        requires_product: Whether a product structure is mandatory.
        description: Human-readable summary.
        supported: Whether the strategy is implemented in this release
            (endpoint-path is a declared-but-unimplemented hook).
    """

    name: str
    requires_product: bool = False
    description: str = ""
    supported: bool = True


PATH_STRATEGIES: dict[str, PathStrategySpec] = {
    "guided-scan": PathStrategySpec(
        name="guided-scan",
        requires_product=False,
        description=(
            "Drive user-defined reaction coordinates with a synchronous "
            "xTB relaxed scan (ACP default, most general)."
        ),
    ),
    "rph-reverse": PathStrategySpec(
        name="rph-reverse",
        requires_product=True,
        description=(
            "Product-driven reverse scan (RPH PEB): scan forming bonds from "
            "the product side, refine with B97-3c SP, select TS/INT seeds."
        ),
    ),
    "direct-ts": PathStrategySpec(
        name="direct-ts",
        requires_product=False,
        description="Use a user-supplied TS guess directly (skip path search).",
    ),
    "endpoint-path": PathStrategySpec(
        name="endpoint-path",
        requires_product=True,
        description="Future NEB / Growing-String path (declared, not yet implemented).",
        supported=False,
    ),
}

PATH_STRATEGY_IDS: tuple[str, ...] = tuple(PATH_STRATEGIES.keys())
FIDELITY_IDS: tuple[str, ...] = tuple(FIDELITY_PROFILES.keys())

_DEFAULT_STRATEGY: PathStrategy = "guided-scan"
_DEFAULT_FIDELITY: Fidelity = "s3"


def resolve_strategy(value: str | None) -> str:
    """Coerce a user-provided strategy to a known id (default guided-scan)."""
    if not value:
        return _DEFAULT_STRATEGY
    norm = str(value).strip().lower()
    if norm not in PATH_STRATEGIES:
        return _DEFAULT_STRATEGY
    return cast(PathStrategy, norm)


def resolve_fidelity(value: str | None) -> Fidelity:
    """Coerce a user-provided fidelity to a known id (default s3)."""
    if not value:
        return _DEFAULT_FIDELITY
    norm = str(value).strip().lower()
    if norm not in FIDELITY_PROFILES:
        return _DEFAULT_FIDELITY
    return cast(Fidelity, norm)


def rph_profile_id(fidelity: FidelityProfile | str) -> str:
    """Return the canonical RPH profile id for an ACP fidelity selector.

    Args:
        fidelity: ACP fidelity id/profile or an already-resolved RPH profile id.

    Returns:
        The RPH profile id understood by ReactionProfileHunter.

    Raises:
        ValueError: If the fidelity name is unknown.
    """

    raw_name = fidelity.name if isinstance(fidelity, FidelityProfile) else str(fidelity)
    normalized = raw_name.strip().lower()
    if normalized in RPH_PROFILE_IDS:
        return RPH_PROFILE_IDS[normalized]
    if raw_name in RPH_PROFILE_IDS.values():
        return raw_name
    raise ValueError(f"Unsupported RPH fidelity mapping: {raw_name!r}")


def resolve_fidelity_profile(_strategy: str, fidelity: Fidelity) -> FidelityProfile:
    """Return the fidelity profile for a resolved (strategy, fidelity) pair.

    ``direct-ts`` skips the scan, so its scan-point default is irrelevant;
    every other strategy uses the fidelity's ``scan_points``.
    """
    return FIDELITY_PROFILES[fidelity]


def mechanism_profile_ids() -> tuple[str, ...]:
    """Return the catalog's mechanism preset profile ids (rph-s3 / rph-s4 / ...)."""
    from acp.catalog import METHOD_SCHEMAS

    profiles = METHOD_SCHEMAS.get("mechanism", {}).get("profiles")
    return tuple(
        str(profile.get("profile_id"))
        for profile in profiles
        if isinstance(profile, dict) and profile.get("profile_id")
    )


def resolve_preset(preset_id: str | None) -> tuple[str | None, str | None]:
    """Return ``(strategy, fidelity)`` defaults for a catalog RPH preset.

    Args:
        preset_id: Catalog profile id (for example ``"rph-s3"``).

    Returns:
        ``(strategy, fidelity)`` — both ``None`` when the preset is unknown.
    """
    if not preset_id:
        return None, None
    from acp.catalog import METHOD_SCHEMAS

    profiles = METHOD_SCHEMAS.get("mechanism", {}).get("profiles")
    for profile in profiles if isinstance(profiles, list) else []:
        if not isinstance(profile, dict) or str(profile.get("profile_id") or "") != preset_id:
            continue
        levels = profile.get("levels")
        if not isinstance(levels, dict):
            return None, None
        scan = _profile_level(levels, "scan")
        strategy = _optional_str(scan.get("path_strategy"))
        fidelity = _optional_str(scan.get("fidelity"))
        return strategy, fidelity
    return None, None


__all__ = [
    "FIDELITY_IDS",
    "FIDELITY_PROFILES",
    "FidelityProfile",
    "PATH_STRATEGIES",
    "PATH_STRATEGY_IDS",
    "PathStrategySpec",
    "RPH_CENSO_LITE_MODE",
    "RPH_PROFILE_IDS",
    "XTB_FAST_MODE",
    "mechanism_profile_ids",
    "resolve_fidelity",
    "resolve_fidelity_profile",
    "resolve_preset",
    "resolve_strategy",
    "rph_profile_id",
]
