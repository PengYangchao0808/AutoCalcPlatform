"""Unit tests for ``acp.chem.composition`` (Hessian default utilities).

Covers plan §14.1 / §14.2: element classification, normalize/validate,
resolve with provenance, lazy caching, and graded defaults by element.
"""

from __future__ import annotations

import pytest

from acp.chem.composition import (
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
# normalize_recalc_hess
# ---------------------------------------------------------------------------


class TestNormalizeRecalcHess:
    def test_none_and_empty(self):
        assert normalize_recalc_hess(None) is None
        assert normalize_recalc_hess("") is None
        assert normalize_recalc_hess("   ") is None

    def test_auto_case_insensitive(self):
        assert normalize_recalc_hess("auto") == "auto"
        assert normalize_recalc_hess("AUTO") == "auto"
        assert normalize_recalc_hess("Auto") == "auto"
        assert normalize_recalc_hess(AUTO_RECALC_HESS) == "auto"

    def test_zero(self):
        assert normalize_recalc_hess(0) == 0
        assert normalize_recalc_hess("0") == 0

    @pytest.mark.parametrize("val", [1, 5, 10, 100, 1000, "1", "5", "1000"])
    def test_positive_int_valid(self, val):
        assert normalize_recalc_hess(val) == int(val)

    @pytest.mark.parametrize("val", [True, False])
    def test_bool_rejected(self, val):
        with pytest.raises(ValueError):
            normalize_recalc_hess(val)

    @pytest.mark.parametrize("val", [1.0, 20.5, -3.0, 0.0])
    def test_float_rejected(self, val):
        with pytest.raises(ValueError):
            normalize_recalc_hess(val)

    @pytest.mark.parametrize("val", [-1, 1001, "-1", "1001"])
    def test_out_of_range_rejected(self, val):
        with pytest.raises(ValueError):
            normalize_recalc_hess(val)

    @pytest.mark.parametrize("val", ["fast", "none", "1.5", "0x5", "5.0", "1e3", "-5"])
    def test_invalid_string_rejected(self, val):
        with pytest.raises(ValueError):
            normalize_recalc_hess(val)

    @pytest.mark.parametrize("val", [[1], {"a": 1}, object()])
    def test_invalid_type_rejected(self, val):
        with pytest.raises(ValueError):
            normalize_recalc_hess(val)


# ---------------------------------------------------------------------------
# classify_symbols / is_light_element_molecule
# ---------------------------------------------------------------------------


class TestClassifySymbols:
    def test_light_only(self):
        heavy, triggering = classify_symbols(["C", "H", "O", "N"])
        assert heavy == set() and triggering == set()

    def test_halogen_is_light(self):
        # Halogens (F/Cl/Br/I) are in the light set (D1).
        heavy, triggering = classify_symbols(["C", "Cl", "Br", "F", "I"])
        assert heavy == set() and triggering == set()

    def test_heteroatom_only(self):
        heavy, triggering = classify_symbols(["C", "H", "S", "P"])
        assert heavy == {"S", "P"}
        # P/S are heteroatoms → not triggering (benign PES).
        assert triggering == set()

    def test_metal_triggers(self):
        heavy, triggering = classify_symbols(["Fe", "Cl", "Cl"])
        # Cl is light; Fe is the only heavy + triggering element.
        assert heavy == {"Fe"}
        assert triggering == {"Fe"}

    def test_si_is_heteroatom_not_triggering(self):
        heavy, triggering = classify_symbols(["Si", "C", "H"])
        assert heavy == {"Si"}
        assert triggering == set()

    @pytest.mark.parametrize("symbols", [[], None])  # type: ignore[arg-type]
    def test_empty_rejected(self, symbols):
        with pytest.raises(ValueError):
            classify_symbols(symbols)

    def test_blank_entries_rejected(self):
        with pytest.raises(ValueError):
            classify_symbols(["C", "", "H"])

    def test_case_normalised(self):
        heavy, _ = classify_symbols(["fe", "cL"])
        assert heavy == {"Fe"}

    def test_is_light_element_molecule(self):
        assert is_light_element_molecule(["C", "H", "H", "O"]) is True
        assert is_light_element_molecule(["C", "Cl"]) is True
        assert is_light_element_molecule(["C", "S"]) is False
        assert is_light_element_molecule(["Fe"]) is False


# ---------------------------------------------------------------------------
# default_recalc_hess_for_symbols
# ---------------------------------------------------------------------------


class TestGradedDefaults:
    """Two-tier default policy: light → 0 (no exact Hessian ever),
    any non-light element (P/S/Si/B, metals, …) → 10."""

    def test_light_returns_zero(self):
        assert default_recalc_hess_for_symbols(["C", "H", "O", "N", "F", "Cl", "Br", "I"]) == 0

    def test_heteroatom_only_returns_10(self):
        # AC4b: TMS (Si only) → 10
        assert default_recalc_hess_for_symbols(["Si", "C", "H"]) == NON_LIGHT_DEFAULT_INTERVAL
        # AC2: DMSO (S only beyond light) → 10
        assert (
            default_recalc_hess_for_symbols(["C", "C", "S", "O", "H"]) == NON_LIGHT_DEFAULT_INTERVAL
        )
        assert default_recalc_hess_for_symbols(["P", "C", "H"]) == NON_LIGHT_DEFAULT_INTERVAL
        assert default_recalc_hess_for_symbols(["B", "C", "H"]) == NON_LIGHT_DEFAULT_INTERVAL

    def test_metal_returns_10(self):
        # AC4: FeCl2 → 10 (all non-light elements share the same tier)
        assert default_recalc_hess_for_symbols(["Fe", "Cl", "Cl"]) == NON_LIGHT_DEFAULT_INTERVAL
        assert default_recalc_hess_for_symbols(["Cu", "C", "H"]) == NON_LIGHT_DEFAULT_INTERVAL

    def test_metal_and_heteroatom_returns_10(self):
        assert default_recalc_hess_for_symbols(["Fe", "S", "C"]) == NON_LIGHT_DEFAULT_INTERVAL

    def test_none_symbols_rejected_for_auto(self):
        with pytest.raises(ValueError):
            default_recalc_hess_for_symbols(None)


# ---------------------------------------------------------------------------
# resolve_recalc_hess
# ---------------------------------------------------------------------------


class TestResolve:
    def test_explicit_int(self):
        r = resolve_recalc_hess(explicit=5, symbols=["C", "H"])
        assert r.interval == 5
        assert r.source == "explicit"
        assert r.reason == "explicit_interval"
        assert r.enabled is True

    def test_explicit_zero(self):
        r = resolve_recalc_hess(explicit=0, symbols=["Fe"])
        assert r.interval == 0
        assert r.source == "explicit"
        assert r.reason == "explicit_off"
        assert r.enabled is False

    def test_explicit_auto_light(self):
        r = resolve_recalc_hess(explicit="auto", symbols=["C", "H"])
        assert r.interval == 0
        assert r.reason == "auto"
        assert r.heavy_elements == []

    def test_explicit_auto_metal(self):
        r = resolve_recalc_hess(explicit="auto", symbols=["Fe", "Cl"])
        assert r.interval == 10
        assert r.reason == "auto"
        assert r.heavy_elements == ["Fe"]
        assert r.triggering_elements == ["Fe"]

    def test_config_int_wins_when_explicit_missing(self):
        # AC20: legacy config int 10 → still 10
        r = resolve_recalc_hess(explicit=None, configured=10, symbols=["C", "H"])
        assert r.interval == 10
        assert r.source == "config"
        assert r.reason == "explicit_interval"

    def test_explicit_overrides_config(self):
        # AC8: config 7, explicit auto → element inference wins
        r = resolve_recalc_hess(explicit="auto", configured=7, symbols=["C", "H"])
        assert r.interval == 0
        assert r.source == "explicit"

    def test_both_missing_falls_to_element_inference(self):
        r = resolve_recalc_hess(explicit=None, configured=None, symbols=["Fe"])
        assert r.interval == 10
        assert r.source == "config"
        assert r.reason == "auto"

    def test_auto_without_symbols_raises(self):
        with pytest.raises(ValueError):
            resolve_recalc_hess(explicit="auto", configured=None, symbols=None)
        # And when falling through to element inference without symbols.
        with pytest.raises(ValueError):
            resolve_recalc_hess(explicit=None, configured=None, symbols=None)

    def test_config_auto_with_light(self):
        # Default behaviour: config 'auto', CHON molecule → no recalc.
        r = resolve_recalc_hess(explicit=None, configured="auto", symbols=["C", "H", "O"])
        assert r.interval == 0
        assert r.reason == "auto"

    def test_invalid_explicit_raises(self):
        with pytest.raises(ValueError):
            resolve_recalc_hess(explicit="fast", symbols=["C"])
        with pytest.raises(ValueError):
            resolve_recalc_hess(explicit=-1, symbols=["C"])

    def test_resolution_dataclass_is_frozen(self):
        r = resolve_recalc_hess(explicit=5, symbols=["C"])
        with pytest.raises(Exception):
            r.interval = 99  # type: ignore[misc]

    def test_resolution_defaults_heavy_lists_empty_without_symbols(self):
        # Explicit value does not require symbols; heavy lists stay empty.
        r = resolve_recalc_hess(explicit=5, symbols=None)
        assert r.interval == 5
        assert r.heavy_elements == []
        assert r.triggering_elements == []


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


def test_constants_match_plan():
    """Two-tier defaults: light=0, any non-light element=10."""
    assert NON_LIGHT_DEFAULT_INTERVAL == 10
    assert MAX_RECALC_HESS_INTERVAL == 1000
    assert HETEROATOM_ELEMENTS == frozenset({"P", "S", "Si", "B"})
    assert LIGHT_ELEMENTS == frozenset({"C", "H", "O", "N", "F", "Cl", "Br", "I"})


def test_lazy_resolver_cache_returns_same_callable():
    """Plan §14.2: lazy cache must not re-import per call."""
    from conformer_search.qc.interfaces.orca import _get_resolver

    a = _get_resolver()
    b = _get_resolver()
    assert a is b


def test_hessian_resolution_enabled_property():
    assert HessianResolution(interval=5, source="x", reason="y").enabled is True
    assert HessianResolution(interval=0, source="x", reason="y").enabled is False
