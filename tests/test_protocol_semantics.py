from __future__ import annotations

import warnings

import pytest

from conformer_search.core.specs import PROTOCOL_REGISTRY, get_protocol


def test_censo_zero_no_dft_opt() -> None:
    spec = get_protocol("censo-zero")
    assert spec.recipe.run_part2 is False
    assert spec.recipe.select_mode == "rank1"


def test_censo_zero_no_part1() -> None:
    spec = get_protocol("censo-zero")
    assert spec.recipe.run_part1 is False


def test_censo_lite_has_low_cost_sp() -> None:
    spec = get_protocol("censo-lite")
    assert spec.recipe.run_part1 is True
    assert spec.recipe.select_mode == "rank1"
    assert spec.energy.low_cost_sp_method is not None


def test_censo_full_runs_part2_optimization() -> None:
    spec = get_protocol("censo-full")
    assert spec.recipe.run_part2 is True
    assert spec.recipe.select_mode == "boltzmann_ensemble"


def test_censo_full_has_boltzmann_cutoff() -> None:
    spec = get_protocol("censo-full")
    assert spec.recipe.boltzmann_cutoff is not None
    assert spec.recipe.boltzmann_cutoff > 0.5


def test_reference_sp_uses_external_xyz_backend() -> None:
    spec = get_protocol("reference-sp")
    assert spec.search.backend == "external_xyz"


def test_reference_sp_has_high_level_method() -> None:
    spec = get_protocol("reference-sp")
    assert spec.energy.final_sp_method is not None


def test_ext_uses_crest_backend() -> None:
    spec = get_protocol("ext")
    assert spec.search.backend == "crest"


def test_ext_recipe_is_none() -> None:
    spec = get_protocol("ext")
    assert spec.recipe.variant == "none"
    assert spec.recipe.run_part0 is False


def test_allopt_forbids_funnel_deletion() -> None:
    spec = get_protocol("allopt")
    assert spec.recipe.part1_window_kcal is None or spec.recipe.part1_window_kcal >= 100
    assert spec.recipe.part2_window_kcal is None or spec.recipe.part2_window_kcal >= 100


def test_allopt_runs_part2_and_part3() -> None:
    spec = get_protocol("allopt")
    assert spec.recipe.run_part2 is True
    assert spec.recipe.run_part3 is True


def test_reference_sp_spec_uses_external_xyz_backend() -> None:
    spec = get_protocol("reference-sp")
    assert spec.search.backend == "external_xyz"


def test_bare_legacy_names_raise_ambiguity_error() -> None:
    from conformer_search.core.spec_adapter import ProtocolAmbiguityError, resolve_any_protocol
    for name in ["full", "lite", "zero", "benchmark"]:
        with pytest.raises(ProtocolAmbiguityError, match=f"Protocol {name!r} is ambiguous"):
            resolve_any_protocol(name)


def test_censo_full_safe_has_wider_windows_than_full() -> None:
    full = get_protocol("censo-full")
    safe = get_protocol("censo-full-safe")
    assert safe.recipe.part0_window_kcal >= full.recipe.part0_window_kcal
    assert safe.recipe.boltzmann_cutoff >= full.recipe.boltzmann_cutoff


def test_all_protocols_have_search_backend() -> None:
    for name, spec in PROTOCOL_REGISTRY.items():
        assert spec.search.backend in ("crest", "molclus_xtb_md", "external_xyz", "rdkit"), \
            f"{name} has unknown backend: {spec.search.backend}"


def test_all_censo_protocols_have_part3() -> None:
    for name in ["censo-zero", "censo-lite", "censo-full", "censo-full-safe"]:
        spec = get_protocol(name)
        assert spec.recipe.run_part3 is True, f"{name} should run Part3"


def test_spearman_rank_correlation() -> None:
    from acp.workflows.benchmark import _spearman_rank_correlation
    assert _spearman_rank_correlation([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert _spearman_rank_correlation([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]) == pytest.approx(-1.0)


def test_benchmark_levels_defined() -> None:
    from acp.workflows.benchmark import BENCHMARK_LEVELS
    assert "quick" in BENCHMARK_LEVELS
    assert "standard" in BENCHMARK_LEVELS
    assert "strict" in BENCHMARK_LEVELS
    assert len(BENCHMARK_LEVELS["quick"]) < len(BENCHMARK_LEVELS["strict"])
    assert "censo-full-safe" in BENCHMARK_LEVELS["standard"]
    assert "censo-full-safe" in BENCHMARK_LEVELS["strict"]
    assert "reference-sp" in BENCHMARK_LEVELS["strict"]
    assert "allopt" in BENCHMARK_LEVELS["strict"]
