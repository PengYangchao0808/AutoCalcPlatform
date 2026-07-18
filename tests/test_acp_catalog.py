"""Tests for acp.catalog method configuration."""

from __future__ import annotations

from acp.catalog import (
    convert_method_levels_to_protocol_levels,
    get_method_catalog,
    normalize_and_validate_method_config,
)

CONFSEARCH_SCHEMA = {
    "method_levels": [
        {
            "level_id": "single_point",
            "allowed_engines": ["orca"],
            "fields": ["functional", "basis", "solvent_model", "solvent"],
            "required": True,
        }
    ]
}


def test_method_catalog_orca_solvent_model_options() -> None:
    catalog = get_method_catalog()
    field_defs = catalog["field_definitions"]
    orca_options = field_defs["solvent_model"]["per_backend"]["orca"]
    assert orca_options == ["none", "CPCM", "SMD"]
    assert "COSMO" not in orca_options


def test_method_catalog_xtb_solvent_model_options() -> None:
    catalog = get_method_catalog()
    field_defs = catalog["field_definitions"]
    xtb_options = field_defs["solvent_model"]["per_backend"]["xtb"]
    assert xtb_options == ["none", "ALPB", "GBSA"]


def test_normalize_and_validate_accepts_uppercase_solvent_model() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "wB97X-D4",
                "basis": "def2-TZVPP",
                "solvent_model": "SMD",
                "solvent": "toluene",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(method, CONFSEARCH_SCHEMA)
    assert not errors
    assert levels["single_point"]["solvent_model"] == "smd"


def test_normalize_and_validate_rejects_unknown_solvent_model() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "wB97X-D4",
                "basis": "def2-TZVPP",
                "solvent_model": "COSMO",
                "solvent": "toluene",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(method, CONFSEARCH_SCHEMA)
    assert errors
    assert "COSMO" in errors[0]


def test_convert_method_levels_lowercases_solvent_model() -> None:
    levels = {
        "dft_opt": {
            "engine": "orca",
            "functional": "r2SCAN-3c",
            "basis": "def2-SVP",
            "solvent_model": "CPCM",
            "solvent": "toluene",
        },
        "single_point": {
            "engine": "orca",
            "functional": "wB97X-D4",
            "basis": "def2-TZVPP",
            "solvent_model": "SMD",
            "solvent": "toluene",
        },
    }
    converted = convert_method_levels_to_protocol_levels(levels)
    assert converted["optimization"]["solvent_model"] == "cpcm"
    assert converted["single_point"]["solvent_model"] == "smd"


def test_normalize_none_clears_solvent() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "wB97X-D4",
                "basis": "def2-TZVPP",
                "solvent_model": "NONE",
                "solvent": "toluene",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(method, CONFSEARCH_SCHEMA)
    assert not errors
    assert levels["single_point"]["solvent_model"] == "none"
    assert levels["single_point"]["solvent"] == ""
