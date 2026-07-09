"""
Conformer Workflow Specifications
==================================

Composable architecture: ConformerWorkflowSpec = SearchProfile + CensoRecipe
+ EnergyProfile + ThermoProfile + BenchmarkSuite.

Author: QCcalc Team
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# SearchProfile — where the conformer ensemble comes from
# ---------------------------------------------------------------------------

SearchBackend = Literal["rdkit", "crest", "molclus_xtb_md", "external_xyz"]
SearchMode = Literal["quick", "standard", "exhaustive"]
Clusterer = Literal["isostat", "cregen", "molclus", "rmsd"]


@dataclass(frozen=True)
class SearchProfile:
    """Search backend configuration."""

    backend: SearchBackend = "crest"
    mode: SearchMode = "standard"
    clusterer: Clusterer = "isostat"
    n_initial_cap: int | None = None
    n_cluster_cap: int | None = 48
    initial_window_kcal: float | None = 8.0
    two_stage_enabled: bool = True


# ---------------------------------------------------------------------------
# CensoRecipe — the CENSO-like funnel refinement variant
# ---------------------------------------------------------------------------

CensoVariant = Literal["none", "zero", "lite", "full", "refine"]
ProtocolFamily = Literal["ext", "censo", "reference"]


@dataclass(frozen=True)
class CensoRecipe:
    """CENSO-style multi-pass refinement recipe."""

    variant: CensoVariant = "none"

    # Which parts to run
    run_part0: bool = False        # Cheap prescreening (xTB energy window)
    run_part1: bool = False        # Low-cost DFT SP screening / reranking
    run_part2: bool = False        # DFT geometry optimization + free-energy eval
    run_part3: bool = False        # High-level refinement (final SP + Shermo)

    # Selection mode after all parts
    select_mode: Literal["rank1", "topN", "boltzmann_ensemble"] = "rank1"

    # Thresholds (kcal/mol, None = no filtering at that stage)
    part0_window_kcal: float | None = None
    part1_window_kcal: float | None = None
    part2_window_kcal: float | None = None
    boltzmann_cutoff: float | None = None

    # Special flags
    top2_fallback_enabled: bool = False
    top2_gap_kcal: float | None = 1.0


# ---------------------------------------------------------------------------
# EnergyProfile — QC method stack
# ---------------------------------------------------------------------------

ThermoBackendLiteral = Literal["none", "shermo", "xtb_mrrho", "orca_freq"]


@dataclass(frozen=True)
class EnergyProfile:
    """Levels of theory for each stage."""

    xtb_method: str = "gfn2"
    low_cost_sp_method: str | None = "r2scan3c"
    opt_method: str | None = "r2scan3c"
    final_sp_method: str | None = "wb97x-d4"
    final_basis: str | None = "def2-tzvpp"
    solvent_model: str | None = None

    def as_stage_overrides(self) -> dict[str, dict[str, object]]:
        overrides: dict[str, dict[str, object]] = {}
        if self.low_cost_sp_method is not None:
            overrides["low_cost_sp"] = {
                "method": self.low_cost_sp_method,
                "basis": self.final_basis,
                "solvent_model": self.solvent_model,
            }
        if self.opt_method is not None:
            overrides["optimization"] = {
                "method": self.opt_method,
                "basis": self.final_basis,
                "solvent_model": self.solvent_model,
            }
        if self.final_sp_method is not None:
            overrides["final_sp"] = {
                "method": self.final_sp_method,
                "basis": self.final_basis,
                "solvent_model": self.solvent_model,
            }
        return overrides


@dataclass(frozen=True)
class ThermoProfile:
    """Thermochemistry backend configuration."""

    backend: ThermoBackendLiteral = "none"
    temperature: float = 298.15
    pressure: float = 1.0
    qrrho_model: str | None = None
    low_freq_cutoff: float | None = None


# ---------------------------------------------------------------------------
# Result selection modes
# ---------------------------------------------------------------------------

ResultMode = Literal["all_conformers", "best_per_rank", "ensemble"]


@dataclass(frozen=True)
class ResourcePolicy:
    """Resource allocation policy."""

    nproc: int = 16
    mem_gb: int = 32
    walltime_h: int = 168


# ---------------------------------------------------------------------------
# BenchmarkSuite — multi-protocol comparison
# ---------------------------------------------------------------------------

BenchmarkLevel = Literal["none", "quick", "standard", "strict"]
BenchmarkMetric = Literal[
    "global_min_identity",
    "deltaG_vs_reference",
    "rank_spearman",
    "boltzmann_overlap",
    "walltime",
    "n_sp",
    "n_opt",
    "n_freq",
    "failure_rate",
]


@dataclass
class BenchmarkSuite:
    """Benchmark configuration for running multiple protocols."""

    level: BenchmarkLevel = "none"
    protocols: list[str] = field(default_factory=list)
    reference_protocol: str = "censo-full"
    compare_metrics: list[BenchmarkMetric] = field(default_factory=lambda: [
        "global_min_identity",
        "deltaG_vs_reference",
        "rank_spearman",
        "walltime",
    ])


# ---------------------------------------------------------------------------
# ConformerWorkflowSpec — top-level spec composing all profiles
# ---------------------------------------------------------------------------

@dataclass
class ConformerWorkflowSpec:
    """Complete conformer workflow specification.

    Composes search strategy + CENSO recipe + energy levels +
    thermochemistry + resources into a single spec.
    """

    name: str
    search: SearchProfile = field(default_factory=SearchProfile)
    recipe: CensoRecipe = field(default_factory=CensoRecipe)
    energy: EnergyProfile = field(default_factory=EnergyProfile)
    thermo: ThermoProfile = field(default_factory=ThermoProfile)
    resource: ResourcePolicy = field(default_factory=ResourcePolicy)
    benchmark: BenchmarkSuite | None = None
    family: ProtocolFamily = "censo"


# ---------------------------------------------------------------------------
# Built-in protocol presets
# ---------------------------------------------------------------------------

# ext — CREST conformer search + ISOSTAT clustering
EXT = ConformerWorkflowSpec(
    name="ext",
    family="ext",
    search=SearchProfile(
        backend="crest",
        mode="standard",
        clusterer="isostat",
        n_cluster_cap=48,
        initial_window_kcal=8.0,
        two_stage_enabled=True,
    ),
    recipe=CensoRecipe(
        variant="none",
        run_part0=False, run_part1=False,
        run_part2=False, run_part3=False,
        select_mode="topN",
    ),
    energy=EnergyProfile(final_sp_method=None),
    thermo=ThermoProfile(backend="none"),
)

# censo-zero — xTB rank1 economical recipe
CENSO_ZERO = ConformerWorkflowSpec(
    name="censo-zero",
    family="censo",
    search=SearchProfile(
        backend="crest", mode="standard",
        clusterer="isostat", n_cluster_cap=24, initial_window_kcal=6.0,
        two_stage_enabled=False,
    ),
    recipe=CensoRecipe(
        variant="zero",
        run_part0=True, run_part1=False,
        run_part2=False, run_part3=True,
        select_mode="rank1",
        part0_window_kcal=6.0,
    ),
    energy=EnergyProfile(low_cost_sp_method=None, opt_method=None,
                         final_sp_method="wb97x-d4", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="none"),
)

# censo-lite — low-cost DFT SP rerank economical recipe
CENSO_LITE = ConformerWorkflowSpec(
    name="censo-lite",
    family="censo",
    search=SearchProfile(
        backend="crest", mode="standard",
        clusterer="isostat", n_cluster_cap=24, initial_window_kcal=6.0,
        two_stage_enabled=False,
    ),
    recipe=CensoRecipe(
        variant="lite",
        run_part0=True, run_part1=True,
        run_part2=False, run_part3=True,
        select_mode="rank1",
    ),
    energy=EnergyProfile(low_cost_sp_method="r2scan3c", opt_method=None,
                         final_sp_method="wb97x-d4", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="none"),
)

# censo-full — Grimme-style Part0–Part3 full CENSO recipe
CENSO_FULL = ConformerWorkflowSpec(
    name="censo-full",
    family="censo",
    search=SearchProfile(
        backend="crest", mode="standard",
        clusterer="isostat", n_cluster_cap=48, initial_window_kcal=8.0,
        two_stage_enabled=False,
    ),
    recipe=CensoRecipe(
        variant="full",
        run_part0=True, run_part1=True,
        run_part2=True, run_part3=True,
        select_mode="boltzmann_ensemble",
        part0_window_kcal=4.0,
        part1_window_kcal=3.5,
        part2_window_kcal=3.0,
        boltzmann_cutoff=0.95,
        top2_fallback_enabled=False,
    ),
    energy=EnergyProfile(low_cost_sp_method="r2scan3c", opt_method="r2scan3c",
                         final_sp_method="wb97x-d4", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="shermo"),
)

# censo-full-safe — relaxed thresholds for reactive / ionic systems
CENSO_FULL_SAFE = ConformerWorkflowSpec(
    name="censo-full-safe",
    family="censo",
    search=SearchProfile(
        backend="crest", mode="standard",
        clusterer="isostat", n_cluster_cap=48, initial_window_kcal=8.0,
        two_stage_enabled=False,
    ),
    recipe=CensoRecipe(
        variant="full",
        run_part0=True, run_part1=True,
        run_part2=True, run_part3=True,
        select_mode="boltzmann_ensemble",
        part0_window_kcal=8.0,
        part1_window_kcal=6.0,
        part2_window_kcal=4.0,
        boltzmann_cutoff=0.99,
        top2_fallback_enabled=False,
    ),
    energy=EnergyProfile(low_cost_sp_method="r2scan3c", opt_method="r2scan3c",
                         final_sp_method="wb97x-d4", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="shermo"),
)

# allopt — exhaustive DFT validation
ALLOPT = ConformerWorkflowSpec(
    name="allopt",
    family="censo",
    search=SearchProfile(
        backend="crest", mode="standard",
        clusterer="isostat", n_cluster_cap=48, initial_window_kcal=8.0,
    ),
    recipe=CensoRecipe(
        variant="none",
        run_part0=False, run_part1=False,
        run_part2=True, run_part3=True,
        select_mode="boltzmann_ensemble",
        part1_window_kcal=None,
        part2_window_kcal=None,
    ),
    energy=EnergyProfile(opt_method="r2scan3c",
                         final_sp_method="wb97x-d4", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="shermo"),
)

# reference-sp — high-level SP refinement on existing ensemble
REFERENCE_SP = ConformerWorkflowSpec(
    name="reference-sp",
    family="reference",
    search=SearchProfile(backend="external_xyz", mode="quick", two_stage_enabled=False),
    recipe=CensoRecipe(
        variant="refine",
        run_part0=False, run_part1=False,
        run_part2=False, run_part3=True,
        select_mode="boltzmann_ensemble",
    ),
    energy=EnergyProfile(opt_method=None,
                         final_sp_method="dlpno-ccsdt", final_basis="def2-tzvpp"),
    thermo=ThermoProfile(backend="shermo"),
)

# Registry of all built-in presets
PROTOCOL_REGISTRY: dict[str, ConformerWorkflowSpec] = {
    "ext": EXT,
    "censo-zero": CENSO_ZERO,
    "censo-lite": CENSO_LITE,
    "censo-full": CENSO_FULL,
    "censo-full-safe": CENSO_FULL_SAFE,
    "allopt": ALLOPT,
    "reference-sp": REFERENCE_SP,
}


def get_protocol(name: str) -> ConformerWorkflowSpec:
    """Look up a built-in protocol spec by name."""
    normalized = name.strip().lower()
    if normalized not in PROTOCOL_REGISTRY:
        available = ", ".join(sorted(PROTOCOL_REGISTRY))
        raise KeyError(f"Unknown protocol: {name!r}. Available: {available}")
    return PROTOCOL_REGISTRY[normalized]


__all__ = [
    "SearchProfile",
    "CensoRecipe",
    "EnergyProfile",
    "ThermoProfile",
    "ResourcePolicy",
    "BenchmarkSuite",
    "ConformerWorkflowSpec",
    "CensoVariant",
    "ProtocolFamily",
    "EXT",
    "CENSO_ZERO", "CENSO_LITE", "CENSO_FULL",
    "CENSO_FULL_SAFE", "ALLOPT", "REFERENCE_SP",
    "PROTOCOL_REGISTRY",
    "get_protocol",
]
