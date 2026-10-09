"""Unit tests for ``cccp.qc.hessian_policy`` (Hessian default utilities).

Standalone cccp-side assertions — no ``acp`` imports — pinning the graded
``Recalc_Hess`` policy after its relocation out of ``acp.chem.composition``
(plan todo 6): defaults, explicit overrides, and boundary values.
"""

from __future__ import annotations

import pytest

from cccp.qc.hessian_policy import (
    AUTO_RECALC_HESS,
    HETEROATOM_ELEMENTS,
    LIGHT_ELEMENTS,
    MAX_RECALC_HESS_INTERVAL,
    NON_LIGHT_DEFAULT_INTERVAL,
    HessianResolution,
    classify_symbols,
    default_recalc_hess_for_symbols,
    is_light_element_molecule,
    normalize_recalc_hess,
    resolve_recalc_hess,
)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------


def test_constants_match_plan():
    assert LIGHT_ELEMENTS == frozenset({"C", "H", "O", "N", "F", "Cl", "Br", "I"})
    assert HETEROATOM_ELEMENTS == frozenset({"P", "S", "Si", "B"})
    assert NON_LIGHT_DEFAULT_INTERVAL == 10
    assert MAX_RECALC_HESS_INTERVAL == 1000
    assert AUTO_RECALC_HESS == "auto"


# ---------------------------------------------------------------------------
# normalize_recalc_hess — defaults / explicit / boundaries
# ---------------------------------------------------------------------------


def test_normalize_default_means_follow_config():
    assert normalize_recalc_hess(None) is None
    assert normalize_recalc_hess("") is None
    assert normalize_recalc_hess("   ") is None


def test_normalize_auto_case_insensitive():
    assert normalize_recalc_hess("auto") == "auto"
    assert normalize_recalc_hess("AUTO") == "auto"
    assert normalize_recalc_hess(AUTO_RECALC_HESS) == "auto"


def test_normalize_zero_and_positive():
    assert normalize_recalc_hess(0) == 0
    assert normalize_recalc_hess("0") == 0
    assert normalize_recalc_hess(5) == 5
    assert normalize_recalc_hess("10") == 10
    assert normalize_recalc_hess(" 10 ") == 10


def test_normalize_boundaries():
    assert normalize_recalc_hess(1) == 1
    assert normalize_recalc_hess(MAX_RECALC_HESS_INTERVAL) == 1000
    assert normalize_recalc_hess("1000") == 1000
    with pytest.raises(ValueError):
        normalize_recalc_hess(-1)
    with pytest.raises(ValueError):
        normalize_recalc_hess(1001)
    with pytest.raises(ValueError):
        normalize_recalc_hess("1001")


def test_normalize_rejects_bool_float_and_junk():
    for bad in (True, False, 1.0, 20.5, -3.0, "fast", "none", "1.5", "0x5", "1e3", [1], {"a": 1}):
        with pytest.raises(ValueError):
            normalize_recalc_hess(bad)


# ---------------------------------------------------------------------------
# classify_symbols / graded defaults
# ---------------------------------------------------------------------------


def test_classify_light_only():
    heavy, triggering = classify_symbols(["C", "H", "H", "O"])
    assert heavy == set()
    assert triggering == set()
    assert is_light_element_molecule(["C", "H", "H", "O"]) is True


def test_classify_heteroatoms_heavy_but_not_triggering():
    heavy, triggering = classify_symbols(["P", "S", "Si", "B"])
    assert heavy == {"P", "S", "Si", "B"}
    assert triggering == set()
    assert is_light_element_molecule(["P", "S"]) is False


def test_classify_metal_triggers_and_normalises_case():
    heavy, triggering = classify_symbols(["fe", " O "])
    assert heavy == {"Fe"}
    assert triggering == {"Fe"}


def test_classify_rejects_empty_and_blank():
    with pytest.raises(ValueError):
        classify_symbols([])
    with pytest.raises(ValueError):
        classify_symbols(["C", ""])


def test_graded_defaults_two_tier():
    assert default_recalc_hess_for_symbols(["H", "H"]) == 0
    assert default_recalc_hess_for_symbols(["C", "H", "Cl"]) == 0
    assert default_recalc_hess_for_symbols(["P", "C"]) == NON_LIGHT_DEFAULT_INTERVAL
    assert default_recalc_hess_for_symbols(["Fe", "O"]) == NON_LIGHT_DEFAULT_INTERVAL
    with pytest.raises(ValueError):
        default_recalc_hess_for_symbols(None)


# ---------------------------------------------------------------------------
# resolve_recalc_hess — default / explicit / boundary resolution
# ---------------------------------------------------------------------------


def test_resolve_default_both_missing_falls_to_element_inference():
    light = resolve_recalc_hess(symbols=["H", "H"])
    assert (light.interval, light.source, light.reason) == (0, "config", "auto")
    assert light.enabled is False
    heavy = resolve_recalc_hess(symbols=["Fe"])
    assert (heavy.interval, heavy.source, heavy.reason) == (10, "config", "auto")
    assert heavy.enabled is True


def test_resolve_explicit_wins_over_config():
    res = resolve_recalc_hess(explicit=5, configured=10)
    assert (res.interval, res.source, res.reason) == (5, "explicit", "explicit_interval")
    off = resolve_recalc_hess(explicit=0, configured=10)
    assert (off.interval, off.source, off.reason) == (0, "explicit", "explicit_off")
    assert off.enabled is False


def test_resolve_config_used_when_explicit_missing():
    res = resolve_recalc_hess(explicit=None, configured=7)
    assert (res.interval, res.source, res.reason) == (7, "config", "explicit_interval")


def test_resolve_auto_at_explicit_ignores_fixed_config():
    res = resolve_recalc_hess(explicit="auto", configured=10, symbols=["Fe"])
    assert (res.interval, res.source, res.reason) == (10, "explicit", "auto")
    light = resolve_recalc_hess(explicit="auto", configured=10, symbols=["H", "H"])
    assert light.interval == 0


def test_resolve_auto_requires_symbols():
    with pytest.raises(ValueError):
        resolve_recalc_hess(explicit="auto")
    with pytest.raises(ValueError):
        resolve_recalc_hess(configured="auto")


def test_resolve_explicit_boundary_interval():
    res = resolve_recalc_hess(explicit=MAX_RECALC_HESS_INTERVAL)
    assert res.interval == 1000
    with pytest.raises(ValueError):
        resolve_recalc_hess(explicit=MAX_RECALC_HESS_INTERVAL + 1)
    with pytest.raises(ValueError):
        resolve_recalc_hess(explicit="not-a-policy")


def test_resolve_provenance_fields_sorted():
    res = resolve_recalc_hess(explicit="auto", symbols=["Fe", "Al", "P", "H"])
    assert res.heavy_elements == sorted({"Fe", "Al", "P"})
    assert res.triggering_elements == sorted({"Fe", "Al"})
    assert res.heavy_elements == ["Al", "Fe", "P"]


def test_resolution_is_frozen_with_enabled_property():
    res = HessianResolution(interval=5, source="explicit", reason="explicit_interval")
    assert res.enabled is True
    with pytest.raises(Exception):
        res.interval = 9  # type: ignore[misc]
