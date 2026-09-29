# ruff: noqa: E501
"""Regression tests: select-field values are matched case-insensitively
against the catalog option list and canonicalised to the catalog spelling.

Covers ``normalize_and_validate_method_config``:
- single-value select branch (exact miss → case-insensitive canonical match)
- multi-select branch (per-item canonicalisation)
- preservation of catalog defaults and of the custom-value escape hatch
"""

from __future__ import annotations

from acp.catalog import FIELD_DEFINITIONS, get_method_schema, normalize_and_validate_method_config

_BATCH_CASE_INPUTS = {
    "opt_convergence": "verytight",
    "scf_convergence": "verytight",
    "opt_initial_hessian": "MODEL",
    "opt_rescue_policy": "ADAPTIVE",
    "scf_strategy": "SOSCF",
}

_BATCH_EXPECTED_CANONICAL = {
    "opt_convergence": "VeryTight",
    "scf_convergence": "VeryTight",
    "opt_initial_hessian": "model",
    "opt_rescue_policy": "adaptive",
    "scf_strategy": "soscf",
}


def _canonical_option(field: str, value: str) -> str:
    """The catalog option whose lowercase form equals *value*'s lowercase."""
    options = [str(o) for o in FIELD_DEFINITIONS[field]["options"]]
    return next(o for o in options if o.lower() == value.lower())


def test_batch_optimize_select_fields_canonicalize_case_insensitively():
    schema = get_method_schema("batch_optimize")
    assert schema is not None
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"batch": dict(_BATCH_CASE_INPUTS)}}, schema
    )
    assert errors == []
    batch = levels["batch"]
    for field, submitted in _BATCH_CASE_INPUTS.items():
        expected = _canonical_option(field, submitted)
        assert batch[field] == expected, f"{field}: {submitted!r} -> {batch[field]!r}"
        assert batch[field] == _BATCH_EXPECTED_CANONICAL[field]


def test_batch_optimize_omitted_field_falls_back_to_catalog_default():
    schema = get_method_schema("batch_optimize")
    assert schema is not None
    dflt = FIELD_DEFINITIONS["opt_convergence"]["default"]
    expected_default = dflt.get("orca", dflt.get("*", ""))
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"batch": {"engine": "orca"}}}, schema
    )
    assert errors == []
    assert levels["batch"]["opt_convergence"] == expected_default


def test_batch_optimize_genuinely_invalid_value_still_errors():
    schema = get_method_schema("batch_optimize")
    assert schema is not None
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"batch": {"opt_convergence": "SuperTight"}}}, schema
    )
    assert errors, "expected an error for a value matching no option"
    assert any("opt_convergence" in err and "SuperTight" in err for err in errors)
    assert "opt_convergence" not in levels["batch"]


def test_exact_cased_values_pass_through_unchanged():
    schema = get_method_schema("batch_optimize")
    assert schema is not None
    values = {"opt_convergence": "VeryTight", "scf_strategy": "soscf"}
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"batch": dict(values)}}, schema
    )
    assert errors == []
    for field, value in values.items():
        assert levels["batch"][field] == value


def test_nmr_multi_select_items_canonicalize_case_insensitively():
    schema = get_method_schema("nmr")
    assert schema is not None
    options = [str(o) for o in FIELD_DEFINITIONS["nuclei"]["options"]]
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"giaoa": {"engine": "orca", "nuclei": ["1h", "13C"]}}}, schema
    )
    assert errors == []
    assert levels["giaoa"]["nuclei"] == [
        next(o for o in options if o.lower() == "1h"),
        next(o for o in options if o.lower() == "13c"),
    ]


def test_nmr_multi_select_invalid_item_still_errors():
    schema = get_method_schema("nmr")
    assert schema is not None
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"giaoa": {"engine": "orca", "nuclei": ["1H", "37C"]}}}, schema
    )
    assert errors, "expected an error for a multi item matching no option"
    assert any("nuclei" in err for err in errors)
    assert "nuclei" not in levels["giaoa"]
