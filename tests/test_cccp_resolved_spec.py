"""cccp-side tests for ResolvedCalculationSpec (plan todo 7).

No ``acp`` import here: the parameter-resolution rules must be usable from
a bare ``cccp`` tree.  Covers the three mandated tables (source priority /
null semantics / conflict handling), the requested/effective/source/
adjustment_reason provenance, and the clamp-only adapter semantics that
``acp.catalog._clamp_to_functional`` delegates to.
"""

from __future__ import annotations

import ast
from pathlib import Path

from cccp.qc.keyword_registry import KeywordValueError, resolve
from cccp.qc.resolved_spec import (
    CLAMP_FIELDS,
    CONFLICT_RULES,
    NULL_SEMANTICS,
    RESOLVED_FIELDS,
    SOURCE_PRIORITY,
    SOURCE_PRIORITY_TABLE,
    clamp_calculation_fields,
    forced_ri_clears,
    method_field_default,
    resolve_calculation_spec,
)

# ── the three tables ─────────────────────────────────────────────────────


def test_source_priority_table_is_the_single_order() -> None:
    assert SOURCE_PRIORITY == ("explicit", "task_options", "run_config", "method_default")
    assert set(SOURCE_PRIORITY_TABLE) == set(SOURCE_PRIORITY)
    assert all(SOURCE_PRIORITY_TABLE.values())


def test_null_semantics_table_covers_all_four_spellings() -> None:
    assert NULL_SEMANTICS == {
        "omitted": "inherit",
        "None": "inherit",
        '""': "clear",
        '"none"': "off",
        '"__custom__"': "passthrough",
    }


def test_conflict_table_maps_to_the_three_outcomes() -> None:
    assert set(CONFLICT_RULES.values()) <= {"reject", "clamp", "warn"}
    for rule in (
        "case_mismatch",
        "outside_allowed_set",
        "empty_allowed_set",
        "ri_support_forced_clear",
        "aux_outside_allowed_set",
    ):
        assert CONFLICT_RULES[rule] == "clamp", rule
    assert CONFLICT_RULES["unknown_enum_value"] == "reject"
    assert CONFLICT_RULES["gfn_solvent_policy_violation"] == "reject"
    assert CONFLICT_RULES["family_inapplicable_field"] == "warn"


def test_reject_and_warn_families_are_registry_enforced() -> None:
    try:
        resolve("dispersion", "bogus", family="conventional_dft", implementation="orca_dft")
    except KeywordValueError:
        pass
    else:
        raise AssertionError("unknown_enum_value must reject via KeywordValueError")
    try:
        resolve("solvent_model", "GBSA", family="gfn", implementation="orca_external_xtb")
    except KeywordValueError:
        pass
    else:
        raise AssertionError("gfn_solvent_policy_violation must reject via KeywordValueError")
    canonical, warning = resolve(
        "basis", "def2-SVP", family="gfn", implementation="orca_external_xtb"
    )
    assert canonical is None and warning is not None


def test_resolved_fields_cover_solvent_grid_scf() -> None:
    assert CLAMP_FIELDS == (
        "basis",
        "dispersion",
        "ri_approximation",
        "aux_j_basis",
        "aux_c_basis",
        "scf_convergence",
    )
    for passthrough in ("solvent", "solvent_model", "grid", "scf_strategy"):
        assert passthrough in RESOLVED_FIELDS
        assert passthrough not in CLAMP_FIELDS


def test_scf_below_method_floor_promoted() -> None:
    spec = resolve_calculation_spec("DLPNO-CCSD(T)", explicit={"scf_convergence": "loose"})
    res = spec["scf_convergence"]
    assert res.requested == "loose"
    assert res.effective == "tight"
    assert res.adjustment_reason == "scf_below_method_minimum"


def test_scf_at_or_above_floor_untouched() -> None:
    for value in ("tight", "verytight"):
        spec = resolve_calculation_spec("DLPNO-CCSD(T)", explicit={"scf_convergence": value})
        res = spec["scf_convergence"]
        assert res.effective == value
        assert res.adjustment_reason is None


def test_scf_floor_ignored_for_unconstrained_method() -> None:
    spec = resolve_calculation_spec("wB97M-V", explicit={"scf_convergence": "loose"})
    assert spec["scf_convergence"].effective == "loose"


def test_scf_absent_not_promoted() -> None:
    spec = resolve_calculation_spec("DLPNO-CCSD(T)")
    res = spec["scf_convergence"]
    assert res.requested is None
    assert res.effective is None
    assert res.adjustment_reason is None


# ── priority cases (Table ①) ─────────────────────────────────────────────


def test_priority_explicit_beats_task_options_beats_run_config() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "cc-pVTZ"},
        task_options={"basis": "def2-SVP"},
        run_config={"basis": "def2-TZVPP"},
    )
    res = spec["basis"]
    assert res.requested == "cc-pVTZ"
    assert res.effective == "cc-pVTZ"
    assert res.source == "explicit"
    assert res.adjustment_reason is None


def test_priority_task_options_beats_run_config_beats_method_default() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        task_options={"basis": "cc-pVTZ"},
        run_config={"basis": "def2-SVP"},
    )
    res = spec["basis"]
    assert res.requested == "cc-pVTZ"
    assert res.source == "task_options"

    spec = resolve_calculation_spec("B3LYP", run_config={"basis": "def2-SVP"})
    assert spec["basis"].source == "run_config"
    assert spec["basis"].requested == "def2-SVP"

    spec = resolve_calculation_spec("B3LYP")
    assert spec["basis"].source == "method_default"
    assert spec["basis"].requested == "def2-TZVPP"
    assert spec["basis"].effective == "def2-TZVPP"


def test_priority_same_rule_for_solvent_grid_scf() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"solvent": "water", "grid": "DefGrid1", "scf_convergence": "Tight"},
        task_options={"solvent": "toluene", "grid": "DefGrid3", "scf_convergence": "Normal"},
        run_config={"solvent": "none", "grid": "DefGrid2", "scf_convergence": "VeryTight"},
    )
    assert spec["solvent"].effective == "water"
    assert spec["solvent"].source == "explicit"
    assert spec["grid"].effective == "DefGrid1"
    assert spec["scf_convergence"].effective == "Tight"


# ── null semantics cases (Table ②) ───────────────────────────────────────


def test_null_omitted_and_none_inherit_lower_source() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": None},
        run_config={"basis": "cc-pVTZ"},
    )
    res = spec["basis"]
    assert res.requested == "cc-pVTZ"
    assert res.source == "run_config"

    spec = resolve_calculation_spec("B3LYP", run_config={"basis": "cc-pVTZ"})
    assert spec["basis"].requested == "cc-pVTZ"


def test_null_empty_string_clears_and_never_inherits() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": ""},
        run_config={"basis": "cc-pVTZ"},
        task_options={"basis": "def2-SVP"},
    )
    res = spec["basis"]
    assert res.requested == ""
    assert res.effective == ""
    assert res.source == "explicit"
    assert res.adjustment_reason is None


def test_null_none_token_is_off_not_unset() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"dispersion": "none"},
        run_config={"dispersion": "D4"},
    )
    res = spec["dispersion"]
    assert res.requested == "none"
    assert res.effective == "none"
    assert res.source == "explicit"
    assert res.adjustment_reason is None


def test_null_custom_placeholder_passes_through() -> None:
    spec = resolve_calculation_spec("B3LYP", explicit={"basis": "__custom__"})
    res = spec["basis"]
    assert res.effective == "__custom__"
    assert res.adjustment_reason is None


# ── conflict cases (Table ③) ─────────────────────────────────────────────


def test_conflict_case_mismatch_is_canonicalised() -> None:
    spec = resolve_calculation_spec("B3LYP", explicit={"basis": "def2-tzvpp"})
    res = spec["basis"]
    assert res.effective == "def2-TZVPP"
    assert res.adjustment_reason == "case_mismatch"
    assert res.changed


def test_conflict_outside_allowed_set_clamps_to_first() -> None:
    spec = resolve_calculation_spec("B3LYP", explicit={"basis": "6-311G(d)", "dispersion": "xyz"})
    assert spec["basis"].effective == "def2-SV(P)"
    assert spec["basis"].adjustment_reason == "outside_allowed_set"
    assert spec["dispersion"].effective == "none"
    assert spec["dispersion"].adjustment_reason == "outside_allowed_set"


def test_conflict_empty_allowed_set_forces_empty_even_over_none() -> None:
    spec = resolve_calculation_spec("GFN2-xTB", explicit={"basis": "def2-TZVPP"})
    assert spec["basis"].effective == ""
    assert spec["basis"].adjustment_reason == "empty_allowed_set"

    spec = clamp_calculation_fields("GFN2-xTB", {"basis": None})
    assert spec["basis"].requested is None
    assert spec["basis"].effective == ""
    assert spec["basis"].adjustment_reason == "empty_allowed_set"

    spec = resolve_calculation_spec("GFN2-xTB", explicit={"basis": None})
    assert spec["basis"].effective == ""
    assert spec["basis"].source == "method_default"


def test_conflict_ri_support_forced_clear_composite_owns_all() -> None:
    spec = resolve_calculation_spec(
        "r2SCAN-3c",
        explicit={
            "ri_approximation": "RIJCOSX",
            "aux_j_basis": "def2/J",
            "aux_c_basis": "AutoAux",
        },
    )
    assert spec["ri_approximation"].effective == "none"
    assert spec["aux_j_basis"].effective == ""
    assert spec["aux_c_basis"].effective == ""
    for name in ("ri_approximation", "aux_j_basis", "aux_c_basis"):
        assert spec[name].adjustment_reason == "ri_support_forced_clear"


def test_conflict_ri_support_forced_clear_automatic_keeps_aux_c() -> None:
    spec = resolve_calculation_spec(
        "DLPNO-CCSD(T)",
        explicit={
            "ri_approximation": "RIJK",
            "aux_j_basis": "def2/J",
            "aux_c_basis": "cc-pVTZ/C",
        },
    )
    assert spec["ri_approximation"].effective == "none"
    assert spec["aux_j_basis"].effective == ""
    assert spec["aux_c_basis"].effective == "cc-pVTZ/C"
    assert spec["aux_c_basis"].adjustment_reason is None
    assert forced_ri_clears("automatic") == {"ri_approximation": "none", "aux_j_basis": ""}


def test_conflict_aux_outside_allowed_set_uses_derived_default() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "def2-TZVPP", "aux_j_basis": "bogus/J"},
    )
    res = spec["aux_j_basis"]
    assert res.effective == "def2/J"
    assert res.adjustment_reason == "aux_outside_allowed_set"

    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "cc-pVTZ", "aux_j_basis": "bogus/J"},
    )
    assert spec["aux_j_basis"].effective == "AutoAux"


def test_conflict_aux_membership_is_case_sensitive_historical() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "def2-TZVPP", "aux_j_basis": "def2/j"},
    )
    res = spec["aux_j_basis"]
    assert res.effective == "def2/J"
    assert res.adjustment_reason == "aux_outside_allowed_set"


def test_conflict_aux_c_hidden_without_needs_aux_c() -> None:
    spec = resolve_calculation_spec(
        "B3LYP",
        explicit={"basis": "def2-TZVPP", "aux_c_basis": "def2-TZVPP/C"},
    )
    res = spec["aux_c_basis"]
    assert res.effective == ""
    assert res.adjustment_reason == "aux_outside_allowed_set"


def test_conflict_aux_valid_values_survive() -> None:
    spec = resolve_calculation_spec(
        "PWPB95",
        explicit={
            "basis": "def2-TZVPP",
            "aux_j_basis": "AutoAux",
            "aux_c_basis": "def2-TZVPP/C",
        },
    )
    assert spec["aux_j_basis"].effective == "AutoAux"
    assert spec["aux_c_basis"].effective == "def2-TZVPP/C"
    assert spec["aux_j_basis"].adjustment_reason is None
    assert spec["aux_c_basis"].adjustment_reason is None


# ── provenance / same-source consumers ───────────────────────────────────


def test_every_resolution_records_requested_effective_source_reason() -> None:
    spec = resolve_calculation_spec("B3LYP", explicit={"basis": "def2-tzvpp"})
    res = spec["basis"]
    assert res.field == "basis"
    assert res.requested == "def2-tzvpp"
    assert res.effective == "def2-TZVPP"
    assert res.source == "explicit"
    assert res.adjustment_reason == "case_mismatch"
    for res in spec:
        assert res.field in RESOLVED_FIELDS
        assert res.source in SOURCE_PRIORITY or res.source == "absent"


def test_summary_provenance_signature_share_one_source() -> None:
    spec = resolve_calculation_spec("B3LYP", explicit={"basis": "def2-tzvpp"})
    summary = spec.to_summary()
    provenance = spec.to_provenance()
    signature = spec.cache_signature()
    effective = spec.effective_values()
    assert summary["basis"]["effective"] == effective["basis"] == "def2-TZVPP"
    assert provenance["fields"] == summary
    assert signature["effective"] == effective
    assert signature["method"] == provenance["method"] == "B3LYP"
    assert spec.warnings and "case_mismatch" in spec.warnings[0]


# ── clamp-only adapter semantics (historical _clamp_to_functional) ───────


def test_clamp_adapter_scopes_present_fields_plus_forced() -> None:
    spec = clamp_calculation_fields("DLPNO-CCSD(T)", {"basis": "def2-TZVPP"})
    names = [res.field for res in spec]
    assert names == ["basis", "ri_approximation", "aux_j_basis"]
    assert spec["ri_approximation"].effective == "none"
    assert spec["aux_j_basis"].effective == ""


def test_clamp_adapter_fills_no_method_defaults() -> None:
    spec = clamp_calculation_fields("B3LYP", {"dispersion": "D4"})
    names = [res.field for res in spec]
    assert names == ["dispersion"]
    assert spec.get("basis") is None


def test_clamp_adapter_writeback_matches_historical_table() -> None:
    cases = [
        (
            "B3LYP",
            {"functional": "B3LYP", "dispersion": "d4", "basis": "def2-TZVPP"},
            {"functional": "B3LYP", "dispersion": "D4", "basis": "def2-TZVPP"},
        ),
        (
            "B3LYP",
            {"functional": "B3LYP", "dispersion": "xyz", "basis": "def2-TZVPP"},
            {"functional": "B3LYP", "dispersion": "none", "basis": "def2-TZVPP"},
        ),
        (
            "GFN2-xTB",
            {"functional": "GFN2-xTB", "basis": "def2-TZVPP"},
            {
                "functional": "GFN2-xTB",
                "basis": "",
                "ri_approximation": "none",
                "aux_j_basis": "",
                "aux_c_basis": "",
            },
        ),
        (
            "r2SCAN-3c",
            {"functional": "r2SCAN-3c", "ri_approximation": "RIJCOSX", "aux_j_basis": "def2/J"},
            {
                "functional": "r2SCAN-3c",
                "ri_approximation": "none",
                "aux_j_basis": "",
                "aux_c_basis": "",
            },
        ),
        (
            "B3LYP",
            {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_j_basis": "def2/J"},
            {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_j_basis": "def2/J"},
        ),
        (
            "B3LYP",
            {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_c_basis": "def2-TZVPP/C"},
            {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_c_basis": ""},
        ),
        (
            "DLPNO-CCSD(T)",
            {"functional": "DLPNO-CCSD(T)", "basis": "def2-TZVPP", "aux_c_basis": "cc-pVTZ/C"},
            {
                "functional": "DLPNO-CCSD(T)",
                "basis": "def2-TZVPP",
                "aux_c_basis": "cc-pVTZ/C",
                "ri_approximation": "none",
                "aux_j_basis": "",
            },
        ),
        (
            "unknown-method",
            {"functional": "unknown-method", "basis": "whatever"},
            {"functional": "unknown-method", "basis": "whatever"},
        ),
        (
            "",
            {"functional": "", "basis": "whatever"},
            {"functional": "", "basis": "whatever"},
        ),
        (
            "B3LYP",
            {"functional": "B3LYP", "basis": "__custom__", "dispersion": ""},
            {"functional": "B3LYP", "basis": "__custom__", "dispersion": ""},
        ),
    ]
    for method, level, expected in cases:
        got = dict(level)
        subset = {k: v for k, v in got.items() if k in CLAMP_FIELDS}
        for res in clamp_calculation_fields(method, subset):
            got[res.field] = res.effective
        assert got == expected, (method, level, got, expected)


def test_method_field_default_single_rule() -> None:
    assert method_field_default("basis", "B3LYP") == "def2-TZVPP"
    assert method_field_default("dispersion", "B3LYP") == "D4"
    assert method_field_default("ri_approximation", "r2SCAN-3c") == "none"
    assert method_field_default("ri_approximation", "B3LYP") is None
    assert method_field_default("aux_j_basis", "r2SCAN-3c") == ""
    assert method_field_default("aux_j_basis", "B3LYP", "def2-TZVPP") == "def2/J"
    assert method_field_default("aux_j_basis", "B3LYP", "cc-pVTZ") == "AutoAux"
    assert method_field_default("aux_c_basis", "PWPB95", "def2-TZVPP") == "def2-TZVPP/C"
    assert method_field_default("aux_c_basis", "B3LYP", "def2-TZVPP") == ""
    assert method_field_default("aux_c_basis", "DLPNO-CCSD(T)", "def2-TZVPP") == ""
    assert method_field_default("basis", "not-a-method") is None


# ── boundary: no config re-read, no acp dependency ───────────────────────


def test_resolution_is_pure_and_context_driven() -> None:
    first = resolve_calculation_spec(
        "B3LYP", explicit={"basis": "cc-pVTZ"}, run_config={"dispersion": "D3"}
    )
    second = resolve_calculation_spec(
        "B3LYP", explicit={"basis": "cc-pVTZ"}, run_config={"dispersion": "D3"}
    )
    assert first.to_provenance() == second.to_provenance()
    other = resolve_calculation_spec("B3LYP", explicit={"basis": "cc-pVTZ"})
    assert other["dispersion"].source == "method_default"


def test_module_sources_declare_no_acp_import() -> None:
    pkg = Path(__file__).resolve().parent.parent / "src" / "cccp" / "qc"
    for name in ("method_meta.py", "resolved_spec.py"):
        tree = ast.parse((pkg / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("acp"), (name, alias.name)
            elif isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("acp"), (name, node.module)
