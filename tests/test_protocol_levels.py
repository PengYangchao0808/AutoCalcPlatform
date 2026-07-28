"""Tests for protocol-level method defaults and explicit ``levels`` overrides."""

from __future__ import annotations

from cccp.config import _get_default_config
from cccp.core.protocols import (
    resolve_protocol_spec,
    validate_protocol_methods,
)


def _make_config_with_theory(method: str, basis: str, sp_method: str, sp_basis: str) -> dict:
    """Return a default-ish config with custom theory method/basis."""
    cfg = _get_default_config()
    cfg.setdefault("theory", {}).setdefault("optimization", {})
    cfg["theory"]["optimization"]["method"] = method
    cfg["theory"]["optimization"]["basis"] = basis
    cfg.setdefault("theory", {}).setdefault("single_point", {})
    cfg["theory"]["single_point"]["method"] = sp_method
    cfg["theory"]["single_point"]["basis"] = sp_basis
    return cfg


def test_lite_resolves_to_r2scan3c_and_wb97mv():
    spec = resolve_protocol_spec(_get_default_config(), "lite")
    assert spec.opt_method == "r2SCAN-3c"
    assert spec.opt_basis == ""
    assert spec.final_sp_method == "wB97M-V"
    assert spec.final_sp_basis == "def2-TZVPP"


def test_lite_ignores_custom_theory_methods():
    cfg = _make_config_with_theory("B3LYP", "ma-def2-SVP", "PWPB95 D4", "def2-TZVPD")
    spec = resolve_protocol_spec(cfg, "lite")
    assert spec.opt_method == "r2SCAN-3c"
    assert spec.opt_basis == ""
    assert spec.final_sp_method == "wB97M-V"
    assert spec.final_sp_basis == "def2-TZVPP"


def test_lite_keeps_custom_theory_solvent():
    cfg = _get_default_config()
    cfg.setdefault("theory", {}).setdefault("optimization", {})["solvent"] = "methanol"
    cfg["theory"].setdefault("single_point", {})["solvent"] = "methanol"
    spec = resolve_protocol_spec(cfg, "lite")
    assert spec.opt_method == "r2SCAN-3c"
    assert spec.opt_solvent == "methanol"
    assert spec.sp_solvent == "methanol"


def test_levels_override_methods():
    cfg = _make_config_with_theory("B3LYP", "ma-def2-SVP", "PWPB95 D4", "def2-TZVPD")
    levels = {
        "optimization": {"method": "B3LYP", "basis": "ma-def2-SVP"},
        "single_point": {"method": "PWPB95 D4", "basis": "def2-TZVPD"},
    }
    spec = resolve_protocol_spec(cfg, "lite", levels=levels)
    assert spec.opt_method == "B3LYP"
    assert spec.opt_basis == "ma-def2-SVP"
    assert spec.final_sp_method == "PWPB95 D4"
    assert spec.final_sp_basis == "def2-TZVPD"


def test_levels_override_only_solvent():
    cfg = _get_default_config()
    levels = {
        "optimization": {"solvent": "methanol"},
        "single_point": {"solvent": "methanol"},
    }
    spec = resolve_protocol_spec(cfg, "lite", levels=levels)
    assert spec.opt_method == "r2SCAN-3c"
    assert spec.opt_solvent == "methanol"
    assert spec.sp_solvent == "methanol"


def test_validation_passes_for_default_config():
    cfg = _get_default_config()
    is_valid, errors = validate_protocol_methods(cfg, "lite")
    assert is_valid
    assert errors == []


def test_validation_passes_with_custom_theory():
    cfg = _make_config_with_theory("B3LYP", "ma-def2-SVP", "PWPB95 D4", "def2-TZVPD")
    is_valid, errors = validate_protocol_methods(cfg, "lite")
    assert is_valid
    assert errors == []


def test_validation_fails_when_protocol_config_overrides_method():
    cfg = _get_default_config()
    cfg.setdefault("protocols", {}).setdefault("lite", {})["opt_method"] = "B3LYP"
    is_valid, errors = validate_protocol_methods(cfg, "lite")
    assert not is_valid
    assert any("optimization method" in e for e in errors)


def test_validation_passes_when_levels_override_method():
    cfg = _get_default_config()
    cfg.setdefault("protocols", {}).setdefault("lite", {})["opt_method"] = "B3LYP"
    levels = {"optimization": {"method": "B3LYP"}}
    is_valid, errors = validate_protocol_methods(cfg, "lite", levels=levels)
    assert is_valid
    assert errors == []


def test_levels_are_empty_dict_when_none_passed():
    spec = resolve_protocol_spec(_get_default_config(), "lite", levels=None)
    assert spec.opt_method == "r2SCAN-3c"


def test_parse_long_json_levels():
    """The CLI parser must not treat a long JSON string as a file path."""
    from acp.cli import _parse_levels

    # Construct a syntactically valid JSON object with many repeated keys.
    inner = (
        '{"engine": "orca", "functional": "r2SCAN-3c", "basis": "def2-SVP", '
        '"solvent": "", "solvent_model": "none", "grid": "UltraFine", '
        '"scf_convergence": "Tight", "max_steps": 100, "charge": 0, "multiplicity": 1}'
    )
    entries = ", ".join(f'"stage_{i:03d}": {inner}' for i in range(30))
    long_json = "{" + entries + "}"
    parsed = _parse_levels(long_json)
    assert parsed is not None
    assert "stage_000" in parsed


def test_convert_frontend_levels_to_protocol_levels():
    from acp.catalog import convert_method_levels_to_protocol_levels

    frontend_levels = {
        "dft_opt": {
            "engine": "orca",
            "functional": "r2SCAN-3c",
            "basis": "def2-SVP",
            "solvent": "",
            "solvent_model": "none",
        },
        "single_point": {
            "engine": "orca",
            "functional": "r2SCAN-3c",
            "basis": "def2-SVP",
            "solvent": "",
            "solvent_model": "none",
        },
    }
    converted = convert_method_levels_to_protocol_levels(frontend_levels)
    assert converted["optimization"]["method"] == "r2SCAN-3c"
    assert converted["optimization"]["engine"] == "orca"
    assert converted["single_point"]["method"] == "r2SCAN-3c"
