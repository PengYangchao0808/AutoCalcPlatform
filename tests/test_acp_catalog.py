# ruff: noqa: E501, F811
# pyright: reportAny=false, reportPrivateUsage=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnusedVariable=false, reportRedeclaration=false
"""Tests for acp.catalog method configuration.

Phase 3: Test & Quality Assurance (tests 3.1-3.13 per DevDoc §1.4).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

import pytest

from acp.catalog import (
    FIELD_DEFINITIONS,
    FUNCTIONAL_OPTIONS_MAP,
    METHOD_META,
    METHOD_SCHEMAS,
    WORKFLOW_CATALOG,
    _case_insensitive_get,
    _match_option_case_insensitive,
    convert_method_levels_to_protocol_levels,
    get_method_catalog,
    normalize_and_validate_method_config,
)
from acp.scheduler.jobs import PUBLIC_WORKFLOWS, SUPPORTED_WORKFLOWS

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

_ALL_FUNCTIONALS = frozenset(
    {
        "B3LYP",
        "PBE0",
        "wB97X-D4",
        "wB97M-V",
        "M062X",
        "mPW1PW91",
        "PWPB95",
        "revDSD-PBEP86",
        "r2SCAN-3c",
        "PBEh-3c",
        "B97-3c",
        "DLPNO-CCSD(T)",
        "GFN2-xTB",
        "GFN1-xTB",
        "GFN0-xTB",
        "GFN-FF",
    }
)

_ADVANCED_FIELD_NAMES = frozenset(
    {
        "aux_j_basis",
        "aux_c_basis",
        "ri_approximation",
        "grid",
        "scf_convergence",
        "opt_convergence",
        "max_steps",
        "recalc_hess",
        "scale_factor",
        "minimum_method",
        "minimum_basis",
        "transition_state_method",
        "transition_state_basis",
        "electronic_state",
        "opt_max_iter",
        "opt_trust_radius",
        "opt_initial_hessian",
        "opt_recalc_hess",
        "opt_rescue_policy",
        "opt_max_rescue",
        "minimum_opt_trust_radius",
        "minimum_opt_initial_hessian",
        "minimum_opt_recalc_hess",
        "transition_state_opt_trust_radius",
        "transition_state_opt_initial_hessian",
        "transition_state_opt_recalc_hess",
        "minimum_opt_max_iter",
        "minimum_opt_convergence",
        "minimum_scf_max_iter",
        "minimum_scf_convergence",
        "minimum_scf_strategy",
        "minimum_opt_rescue_policy",
        "minimum_opt_max_rescue",
        "transition_state_opt_max_iter",
        "transition_state_opt_convergence",
        "transition_state_scf_max_iter",
        "transition_state_scf_convergence",
        "transition_state_scf_strategy",
        "transition_state_opt_rescue_policy",
        "transition_state_opt_max_rescue",
        "scf_max_iter",
        "scf_strategy",
        "scf_orbital_inherit",
        "opt_level",
        "md_time_ps",
        "md_dump_fs",
        "md_step_fs",
        "md_hmass",
        "md_shake",
        "md_nvt",
        "md_seed",
        "md_method",
        "conv_check",
        "conv_novelty_max",
        "conv_rmsd",
        "max_frames",
        "opt_gfn",
        "opt_timeout",
        "edis",
        "gdis",
        "resume",
        "tms_shielding_h",
        "tms_shielding_c",
        "scan_points",
        "irc_points",
        "scan_optimizer_grid",
        "scan_optimizer_scf_convergence",
        "scan_optimizer_scf_max_iterations",
        "scan_optimizer_ri_approximation",
        "ts_initial_hessian",
        "conformer_mode",
        "max_elementary_steps",
        "int_extension",
        "promotion_policy",
        "auto_converge",
    }
)


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


def test_pes_scan_catalog_exposes_complete_protocol_levels() -> None:
    catalog = get_method_catalog()
    workflow = next(item for item in WORKFLOW_CATALOG if item["id"] == "PESsearch")
    assert workflow["method_schema_id"] == "pes_scan"
    assert "势能面扫描" in workflow["description_zh"]

    schema = catalog["method_schemas"]["pes_scan"]
    assert [level["level_id"] for level in schema["method_levels"]] == [
        "scan_coordinate",
        "scan_driver",
        "scan_optimizer",
        "single_point",
    ]
    fields = {field_name for level in schema["method_levels"] for field_name in level["fields"]}
    assert {
        "scan_coordinate_kind",
        "scan_bond_type",
        "scan_coordinate_start",
        "scan_coordinate_end",
        "scan_coordinate_points",
        "scan_mode",
        "scan_reuse_previous_geometry",
        "scan_full_scan",
        "scan_failure_policy",
        "scan_retry_count",
        "scan_use_scants",
        "scan_optimizer_method",
        "scan_optimizer_max_iterations",
        "scan_optimizer_convergence",
        "scan_optimizer_retries",
        "scan_optimizer_retry_strategy",
        "functional",
        "basis",
        "dispersion",
        "ri_approximation",
        "aux_j_basis",
        "aux_c_basis",
        "solvent_model",
        "solvent",
        "grid",
        "scf_convergence",
        "single_point_resume",
    }.issubset(fields)
    # The point-optimization iteration limit has one owner. The historical
    # driver-level alias remains backend-compatible but is not rendered as a
    # second, duplicate UI control.
    assert "scan_max_iterations" not in fields
    assert "path_strategy" not in fields
    assert catalog["method_schemas"]["pes_bond_scan"] is schema

    field_defs = catalog["field_definitions"]
    for field_name in schema["method_levels"][1]["fields"]:
        assert field_defs[field_name]["label_zh"]
        assert field_defs[field_name]["help_zh"]
    assert field_defs["scan_mode"]["option_labels_zh"]["relaxed_scan"] == "松弛扫描"


def test_irc_catalog_is_active_simple_workflow() -> None:
    # Given: the public workflow and method catalogs.
    catalog = get_method_catalog()

    # When: the standalone IRC workflow entry is selected.
    workflow = next(item for item in WORKFLOW_CATALOG if item["id"] == "irc")
    schema = catalog["method_schemas"][workflow["method_schema_id"]]

    # Then: it exposes the independent IRC controls and ORCA requirement.
    assert workflow["category"] == "simple"
    assert workflow["status"] == "active"
    assert workflow["default_backend"] == "orca"
    fields = {field_name for level in schema["method_levels"] for field_name in level["fields"]}
    # Method/basis are inherited from the verified TS source (irc_ts_source_v1),
    # not user-selectable; only path controls remain.
    assert {"maxpoints", "step"}.issubset(fields)
    assert "method" not in fields
    assert "basis" not in fields
    assert schema["method_levels"][0]["allowed_engines"] == ["orca"]


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


# =====================================================================
# Phase 3 tests (DevDoc §1.4)
# =====================================================================


# --- 3.1 ---
def test_method_meta_present_in_catalog() -> None:
    """METHOD_META exists in API response and contains all 8 functionals."""
    catalog = get_method_catalog()
    meta = cast(dict[str, object], catalog["method_meta"])
    assert meta is not None
    assert isinstance(meta, dict)
    assert set(meta.keys()) == _ALL_FUNCTIONALS


# --- 3.2 ---
def test_advanced_fields_marked() -> None:
    catalog = get_method_catalog()
    field_defs = catalog["field_definitions"]
    for fn, fd in field_defs.items():
        if fn in _ADVANCED_FIELD_NAMES:
            assert fd.get("advanced") is True, f"{fn} should be advanced"
        else:
            # Not required to be absent, but if present must be False/None
            adv = fd.get("advanced")
            assert adv in (None, False), f"{fn} advanced={adv!r}, expected False/None"


def test_advanced_fields_count_correct() -> None:
    catalog = get_method_catalog()
    field_defs = catalog["field_definitions"]
    advanced = {fn for fn, fd in field_defs.items() if fd.get("advanced")}
    assert advanced == _ADVANCED_FIELD_NAMES, (
        f"expected {len(_ADVANCED_FIELD_NAMES)} advanced fields, got {advanced}"
    )


# --- 3.3 ---
# Built-in dispersion methods expose the built-in type PLUS "none" (users may
# suppress the explicit keyword, e.g. for -3c composites where it is already
# parameterised). The built-in value must remain the first/primary entry.
def test_builtin_dispersion_locking_wb97x_d4() -> None:
    meta = METHOD_META["wB97X-D4"]
    assert meta["builtin_dispersion"] == "D4"
    assert meta["dispersion"] == ("D4", "none")
    assert meta["dispersion"][0] == meta["builtin_dispersion"]


def test_builtin_dispersion_locking_wb97m_v() -> None:
    meta = METHOD_META["wB97M-V"]
    assert meta["builtin_dispersion"] == "VV10"
    assert meta["dispersion"] == ("VV10", "none")
    assert meta["dispersion"][0] == meta["builtin_dispersion"]


def test_builtin_dispersion_null_for_configurable_functionals() -> None:
    for func in ("B3LYP", "PBE0"):
        assert METHOD_META[func]["builtin_dispersion"] is None, (
            f"{func} builtin_dispersion should be None"
        )


# --- 3.4 ---
def test_default_basis_per_functional() -> None:
    assert METHOD_META["B3LYP"]["default_basis"] == "def2-TZVPP"
    assert METHOD_META["DLPNO-CCSD(T)"]["default_basis"] == "def2-TZVPP"
    assert METHOD_META["r2SCAN-3c"]["default_basis"] == "def2-mTZVPP"
    assert METHOD_META["wB97M-V"]["default_basis"] == "def2-TZVPP"


def test_default_dispersion_per_functional() -> None:
    assert METHOD_META["B3LYP"]["default_dispersion"] == "D4"
    assert METHOD_META["DLPNO-CCSD(T)"]["default_dispersion"] == "none"
    # r2SCAN-3c is a composite with D4 built into the parameterisation; the
    # explicit dispersion keyword defaults to "none" (do not double-emit).
    assert METHOD_META["r2SCAN-3c"]["default_dispersion"] == "none"


# --- 3.5 ---
def test_ri_support_classification() -> None:
    composite = ["r2SCAN-3c", "PBEh-3c", "B97-3c"]
    user_no_c = ["B3LYP", "PBE0", "M062X", "mPW1PW91", "wB97X-D4", "wB97M-V"]
    user_with_c = ["PWPB95", "revDSD-PBEP86"]
    automatic = ["DLPNO-CCSD(T)"]

    for func in composite:
        assert METHOD_META[func]["ri_support"] == "composite"
        assert "needs_aux_c" not in METHOD_META[func]
    for func in user_no_c:
        assert METHOD_META[func]["ri_support"] == "user"
        assert METHOD_META[func]["needs_aux_c"] is False
    for func in user_with_c:
        assert METHOD_META[func]["ri_support"] == "user"
        assert METHOD_META[func]["needs_aux_c"] is True
    for func in automatic:
        assert METHOD_META[func]["ri_support"] == "automatic"


_SCHEMA_WITH_RI = {
    "method_levels": [
        {
            "level_id": "single_point",
            "allowed_engines": ["orca"],
            "fields": [
                "functional",
                "basis",
                "dispersion",
                "ri_approximation",
                "aux_j_basis",
                "aux_c_basis",
            ],
            "required": True,
        }
    ]
}


def test_ri_support_composite_forces_ri_none_and_aux_empty() -> None:
    for func in ("r2SCAN-3c", "PBEh-3c", "B97-3c"):
        method = {
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "functional": func,
                    "ri_approximation": "RIJCOSX",
                    "aux_j_basis": "AutoAux",
                }
            }
        }
        levels, errors = normalize_and_validate_method_config(method, _SCHEMA_WITH_RI)
        assert not errors, f"{func}: unexpected errors {errors}"
        assert levels["single_point"]["functional"].lower() == func.lower()


def test_normalize_and_validate_applies_ri_default_when_missing() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "r2SCAN-3c",
                "basis": "",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(method, _SCHEMA_WITH_RI)
    assert not errors
    assert levels["single_point"]["ri_approximation"] == "none"
    assert levels["single_point"]["aux_j_basis"] == ""
    assert levels["single_point"]["aux_c_basis"] == ""


# --- 3.6 ---
_CONFSEARCH_SCHEMA_WITH_DISPERSION = {
    "method_levels": [
        {
            "level_id": "single_point",
            "allowed_engines": ["orca"],
            "fields": ["functional", "basis", "dispersion", "solvent_model", "solvent"],
            "required": True,
        }
    ]
}


def test_validate_rejects_invalid_dispersion() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "wB97M-V",
                "basis": "def2-TZVPP",
                "dispersion": "D3BJ",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(
        method, _CONFSEARCH_SCHEMA_WITH_DISPERSION
    )
    assert errors
    assert any("D3BJ" in e or "dispersion" in e.lower() for e in errors)


def test_validate_accepts_valid_dispersion() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "B3LYP",
                "basis": "def2-TZVPP",
                "dispersion": "D4",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(
        method, _CONFSEARCH_SCHEMA_WITH_DISPERSION
    )
    assert not errors
    assert levels["single_point"]["dispersion"] == "D4"


# --- 3.7 ---
def test_resolve_field_default_respects_method_meta() -> None:
    from acp.catalog import _resolve_field_default

    assert _resolve_field_default("basis", "orca", "B3LYP") == "def2-TZVPP"
    assert _resolve_field_default("dispersion", "orca", "B3LYP") == "D4"
    assert _resolve_field_default("ri_approximation", "orca", "r2SCAN-3c") == "none"
    assert _resolve_field_default("aux_j_basis", "orca", "r2SCAN-3c") == ""
    assert _resolve_field_default("ri_approximation", "orca", "B3LYP") == "RIJCOSX"
    assert _resolve_field_default("aux_j_basis", "orca", "B3LYP", "def2-TZVPP") == "def2/J"
    assert _resolve_field_default("aux_j_basis", "orca", "B3LYP", "cc-pVTZ") == "AutoAux"
    assert _resolve_field_default("aux_c_basis", "orca", "PWPB95", "def2-TZVPP") == "def2-TZVPP/C"
    assert _resolve_field_default("aux_c_basis", "orca", "B3LYP", "def2-TZVPP") == ""


def test_stage_mapping_covers_all_levels() -> None:
    schemas_to_check = ["confsearch", "censo_energy", "dft_optfreqsp"]
    all_level_ids: set[str] = set()
    for sid in schemas_to_check:
        from acp.catalog import get_method_schema

        schema = get_method_schema(sid)
        if schema is None:
            continue
        for ml in schema.get("method_levels", []):
            all_level_ids.add(ml["level_id"])
    converted = convert_method_levels_to_protocol_levels({lid: {} for lid in all_level_ids})
    # stage_mapping converts known stages (dft_opt→optimization, etc.) and
    # passes unknown level_ids through unchanged.  Verify every original
    # level_id is accounted for — either as a mapped key or as a pass-through.
    known_mapping = {
        "dft_opt": "optimization",
        "single_point": "single_point",
        "thermo": "thermo",
        "refinement_sp": "single_point",
        "censo": "single_point",
        "preopt": "optimization",
        "crest": "crest",
        "optfreq": "optimization",
    }
    for lid in all_level_ids:
        expected = known_mapping.get(lid, lid)
        assert expected in converted, (
            f"level_id {lid!r} (→ {expected!r}) was dropped by stage_mapping"
        )


# --- 3.8 ---
def test_functional_keys_three_table_consistent() -> None:
    """FIELD_DEFINITIONS.per_backend.orca, FUNCTIONAL_OPTIONS_MAP, METHOD_META keys are equal."""
    field_def_keys = set(FIELD_DEFINITIONS["functional"]["per_backend"]["orca"])
    fom_keys = set(FUNCTIONAL_OPTIONS_MAP.keys())
    meta_keys = set(METHOD_META.keys())
    assert field_def_keys == meta_keys, (
        f"FIELD_DEFINITIONS vs METHOD_META diff: {field_def_keys ^ meta_keys}"
    )
    assert fom_keys == meta_keys, (
        f"FUNCTIONAL_OPTIONS_MAP vs METHOD_META diff: {fom_keys ^ meta_keys}"
    )


# --- 3.9 ---
def test_dlpno_case_insensitive_match() -> None:
    method = {
        "levels": {
            "single_point": {
                "engine": "orca",
                "functional": "dlpno-ccsd(t)",
                "basis": "def2-TZVPP",
            }
        }
    }
    levels, errors = normalize_and_validate_method_config(method, CONFSEARCH_SCHEMA)
    assert not errors
    assert levels["single_point"]["functional"] == "DLPNO-CCSD(T)"


def test_dlpno_case_insensitive_lookup() -> None:
    assert _case_insensitive_get(METHOD_META, "dlpno-ccsd(t)") is METHOD_META["DLPNO-CCSD(T)"]
    assert _case_insensitive_get(METHOD_META, "DLPNO-CCSD(T)") is METHOD_META["DLPNO-CCSD(T)"]
    assert _case_insensitive_get(METHOD_META, "b3lyp") is METHOD_META["B3LYP"]


# --- 3.10 ---
def test_dlpno_aux_basis_propagated_to_basis_block() -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    config = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }
    iface = ORCAInterface(config, method="DLPNO-CCSD(T)", basis="def2-TZVPP")
    out, _ = iface._build_input_blocks(
        calc_type="sp",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
        aux_c_basis="cc-pVTZ/C",
    )
    assert 'auxC  "cc-pVTZ/C"' in out
    assert "DLPNO-CCSD(T)" in out
    assert "%basis" in out


def test_dlpno_aux_basis_default_auxc() -> None:
    from cccp.qc.interfaces.orca import ORCAInterface

    config = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }
    iface = ORCAInterface(config, method="DLPNO-CCSD(T)", basis="def2-TZVPP")
    out, _ = iface._build_input_blocks(
        calc_type="sp",
        method="DLPNO-CCSD(T)",
        basis="def2-TZVPP",
    )
    assert 'auxC  "def2-TZVPP/C"' in out
    assert 'auxJ  "def2/J"' in out


# --- Phase 4.3: basis_block + extra_blocks for non-DLPNO methods ----------


def test_non_dlpno_method_can_use_extra_blocks_basis_block() -> None:
    """Non-DLPNO methods can also receive a %basis block via extra_blocks (Phase 4.3)."""
    from cccp.qc.interfaces.orca import ORCAInterface

    config = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }
    iface = ORCAInterface(config, method="B3LYP", basis="def2-TZVPP")
    out, _ = iface._build_input_blocks(
        calc_type="sp",
        method="B3LYP",
        basis="def2-TZVPP",
        extra_blocks=["%basis", '  auxJ  "def2/J"', "end"],
    )
    assert "%basis" in out
    assert "auxJ" in out
    assert '"def2/J"' in out
    assert "B3LYP" in out
    assert "def2-TZVPP" in out  # basis still on the ! line


def test_non_dlpno_method_with_structured_override_is_skipped_safely() -> None:
    """Dict entries in extra_blocks are consumed by DLPNO path; for non-DLPNO
    they are safely skipped (Phase 4.3)."""
    from cccp.qc.interfaces.orca import ORCAInterface

    config = {
        "executables": {"orca": {"path": "orca"}},
        "resources": {"nproc": 1, "mem": "1GB"},
    }
    iface = ORCAInterface(config, method="B3LYP", basis="def2-TZVPP")
    out, _ = iface._build_input_blocks(
        calc_type="sp",
        method="B3LYP",
        basis="def2-TZVPP",
        extra_blocks=[{"auxJ": "def2/J"}, "%cpcm", "  smd true", "end"],
    )
    # The dict entry should be skipped; string entries should render
    assert "%cpcm" in out
    assert "smd true" in out
    assert "%basis" not in out  # no DLPNO → no %basis block auto-generated


# --- 3.11 ---
def test_dispersion_case_insensitive_clamp_normalizes() -> None:
    from acp.catalog import _clamp_to_functional

    level = {"functional": "B3LYP", "dispersion": "d4", "basis": "def2-TZVPP"}
    _clamp_to_functional(level, method_key="functional")
    assert level["dispersion"] == "D4"


def test_dispersion_case_insensitive_clamp_rejects_invalid() -> None:
    from acp.catalog import _clamp_to_functional

    level = {"functional": "B3LYP", "dispersion": "xyz", "basis": "def2-TZVPP"}
    _clamp_to_functional(level, method_key="functional")
    assert level["dispersion"] == "none"


def test_match_option_case_insensitive() -> None:
    assert _match_option_case_insensitive(["none", "D3", "D4"], "d4") == (2, "D4")
    assert _match_option_case_insensitive(["none", "D3", "D4"], "xyz") is None
    assert _match_option_case_insensitive(["B3LYP", "PBE0"], "b3lyp") == (0, "B3LYP")


# --- 3.12 ---
def test_supported_workflows_matches_catalog_active() -> None:
    from acp.catalog import get_workflow_catalog

    wf_catalog = get_workflow_catalog()
    active_ids = {w["id"] for w in wf_catalog if w.get("status") == "active"}
    public = set(PUBLIC_WORKFLOWS)
    assert public == active_ids, f"PUBLIC_WORKFLOWS mismatch: {public ^ active_ids}"
    assert "fake" in SUPPORTED_WORKFLOWS


def test_mechanism_entries_retired_and_stage_workflows_active() -> None:
    entry = next(w for w in WORKFLOW_CATALOG if w["id"] == "mechanism")
    assert entry["status"] == "retired"
    assert entry["visible"] is False

    retired_ids = {
        "mech-conf",
        "mech-step",
        "mech-confirm",
        "mech-chain",
        "ensemble",
        "energy",
        "xtbmd_censo_energy",
        "optfreq",
        "optfreqsp",
        "Lowconfirm",
        "Highconfirm",
    }
    for module_id in retired_ids:
        module = next(w for w in WORKFLOW_CATALOG if w["id"] == module_id)
        assert module["status"] == "retired", module_id
        assert module["visible"] is False, module_id

    stage_ids = ["Confsearch", "PESsearch"]
    for stage_id in stage_ids:
        stage = next(w for w in WORKFLOW_CATALOG if w["id"] == stage_id)
        assert stage["status"] == "active", stage_id
        assert stage["category"] == "preset", stage_id
        assert stage["visible"] is True, stage_id
        assert stage_id in SUPPORTED_WORKFLOWS
    assert not [
        w["id"] for w in WORKFLOW_CATALOG if w.get("category") == "stages" and w.get("visible")
    ]

    custom = next(w for w in WORKFLOW_CATALOG if w["id"] == "custom_sequence")
    assert custom["visible"] is False


# --- 3.13 ---
def test_dlpno_basis_locked_single_option() -> None:
    meta = METHOD_META["DLPNO-CCSD(T)"]
    assert meta["basis"] == ("def2-TZVPP",)
    assert len(meta["basis"]) == 1
    assert meta["basis_inline"] is False
    assert meta["ri_support"] == "automatic"
    assert meta["needs_aux_c"] is True
    assert meta["default_aux_j"] == "def2/J"
    assert meta["default_aux_c"] == "def2-TZVPP/C"


def test_dlpno_scf_constraint_declared() -> None:
    from cccp.qc.method_meta import scf_constraint

    constraint = scf_constraint("DLPNO-CCSD(T)")
    assert constraint == {"allowed": ("tight", "verytight"), "default": "tight"}
    assert scf_constraint("wB97M-V") is None


# =====================================================================
# Phase 0a: BASIS_CATALOG ORCA keyword existence gate
# =====================================================================

KNOWN_AUX_J = {"def2/J"}
KNOWN_AUX_C = {
    "def2-SVP/C",
    "def2-SVPD/C",
    "def2-TZVP/C",
    "def2-TZVPP/C",
    "def2-QZVPP/C",
    "cc-pVDZ/C",
    "cc-pVTZ/C",
    "cc-pVQZ/C",
    "cc-pV5Z/C",
    "aug-cc-pVDZ/C",
    "aug-cc-pVTZ/C",
    "aug-cc-pVQZ/C",
}
FORBIDDEN_AUX_C = {"def2-SV(P)/C", "def2-TZVPPD/C", "def2-QZVPPD/C"}


def test_basis_catalog_orca_keyword_existence() -> None:
    from acp.catalog import BASIS_CATALOG

    for basis, meta in BASIS_CATALOG.items():
        if meta.get("aux_j"):
            assert meta["aux_j"] in KNOWN_AUX_J, (
                f"{basis}: aux_j={meta['aux_j']} not in known ORCA /J keywords"
            )
        if meta.get("aux_c"):
            assert meta["aux_c"] in KNOWN_AUX_C, (
                f"{basis}: aux_c={meta['aux_c']} not in known ORCA /C keywords"
            )
            assert meta["aux_c"] not in FORBIDDEN_AUX_C, (
                f"{basis}: aux_c={meta['aux_c']} is a confirmed non-existent ORCA keyword"
            )


# =====================================================================
# Phase 5.1: ri_support classification tests
# =====================================================================


def test_ri_support_classification() -> None:
    composite = ["r2SCAN-3c", "PBEh-3c", "B97-3c"]
    user_no_c = ["B3LYP", "PBE0", "M062X", "mPW1PW91", "wB97X-D4", "wB97M-V"]
    user_with_c = ["PWPB95", "revDSD-PBEP86"]
    automatic = ["DLPNO-CCSD(T)"]

    for func in composite:
        assert METHOD_META[func]["ri_support"] == "composite"
        assert "needs_aux_c" not in METHOD_META[func]
    for func in user_no_c:
        assert METHOD_META[func]["ri_support"] == "user"
        assert METHOD_META[func]["needs_aux_c"] is False
    for func in user_with_c:
        assert METHOD_META[func]["ri_support"] == "user"
        assert METHOD_META[func]["needs_aux_c"] is True
    for func in automatic:
        assert METHOD_META[func]["ri_support"] == "automatic"


def test_basis_catalog_completeness() -> None:
    from acp.catalog import BASIS_CATALOG

    for basis, meta in BASIS_CATALOG.items():
        assert "aux_j" in meta
        assert "aux_c" in meta
        assert meta["aux_j"] is None or isinstance(meta["aux_j"], str)
        assert meta["aux_c"] is None or isinstance(meta["aux_c"], str)


def test_aux_j_default_derives_from_basis() -> None:
    from acp.catalog import _resolve_field_default

    assert _resolve_field_default("aux_j_basis", "orca", "B3LYP", "def2-TZVPP") == "def2/J"
    assert _resolve_field_default("aux_j_basis", "orca", "B3LYP", "cc-pVTZ") == "AutoAux"
    assert _resolve_field_default("aux_j_basis", "orca", "r2SCAN-3c", "def2-mTZVPP") == ""


def test_aux_c_visibility() -> None:
    from acp.catalog import _resolve_field_default

    assert _resolve_field_default("aux_c_basis", "orca", "PWPB95", "def2-TZVPP") == "def2-TZVPP/C"
    assert _resolve_field_default("aux_c_basis", "orca", "B3LYP", "def2-TZVPP") == ""
    assert _resolve_field_default("aux_c_basis", "orca", "DLPNO-CCSD(T)", "def2-TZVPP") == ""


# =====================================================================
# Phase 5.1: normalize_legacy_method tests
# =====================================================================


def test_normalize_legacy_method_normal_dft() -> None:
    from acp.catalog import normalize_legacy_method

    method = {
        "levels": {"sp": {"functional": "B3LYP", "basis": "def2-TZVPP", "aux_basis": "def2/J"}}
    }
    out = normalize_legacy_method(method)
    assert out["levels"]["sp"]["aux_j_basis"] == "def2/J"
    assert "aux_basis" not in out["levels"]["sp"]


def test_normalize_legacy_method_dlpno_routes_to_aux_c() -> None:
    from acp.catalog import normalize_legacy_method

    method = {
        "levels": {
            "sp": {"functional": "DLPNO-CCSD(T)", "basis": "def2-TZVPP", "aux_basis": "cc-pVTZ/C"}
        }
    }
    out = normalize_legacy_method(method)
    assert out["levels"]["sp"]["aux_c_basis"] == "cc-pVTZ/C"
    assert "aux_basis" not in out["levels"]["sp"]


def test_normalize_legacy_method_idempotent() -> None:
    from acp.catalog import normalize_legacy_method

    method = {
        "levels": {
            "sp": {
                "functional": "PWPB95",
                "basis": "def2-TZVPP",
                "aux_j_basis": "def2/J",
                "aux_c_basis": "def2-TZVPP/C",
            }
        }
    }
    out = normalize_legacy_method(method)
    assert out["levels"]["sp"]["aux_j_basis"] == "def2/J"
    assert out["levels"]["sp"]["aux_c_basis"] == "def2-TZVPP/C"
    assert "aux_basis" not in out["levels"]["sp"]


# ---------------------------------------------------------------------------
# Hessian interval field (plan §8)
# ---------------------------------------------------------------------------


def test_recalc_hess_field_is_hessian_interval_type() -> None:
    fd = FIELD_DEFINITIONS["recalc_hess"]
    assert fd["type"] == "hessian_interval"
    assert fd["default"]["*"] == "auto"
    assert fd["widget"] == "hessian_toggle"


@pytest.mark.parametrize(
    "schema_id,level_id",
    [
        ("dft_optimize", "optimize"),
        ("dft_optfreq", "optfreq"),
        ("dft_optfreqsp", "optfreq"),
        ("confsearch", "dft_opt"),
        ("censo_energy", "dft_opt"),
    ],
)
def test_recalc_hess_in_all_opt_schemas(schema_id: str, level_id: str) -> None:
    """Every opt-bearing schema must list recalc_hess (plan §8.2)."""
    from acp.catalog import METHOD_SCHEMAS

    schema = METHOD_SCHEMAS[schema_id]
    level = next(lv for lv in schema["method_levels"] if lv["level_id"] == level_id)
    assert "recalc_hess" in level["fields"], f"{schema_id}.{level_id} missing recalc_hess"


def test_normalize_validates_recalc_hess() -> None:
    schema = {
        "method_levels": [
            {
                "level_id": "optimize",
                "allowed_engines": ["orca"],
                "fields": ["functional", "recalc_hess"],
                "required": True,
            }
        ]
    }
    # auto / 0 / N valid
    for val in ("auto", 0, 5, "5"):
        levels, errors = normalize_and_validate_method_config(
            {
                "levels": {
                    "optimize": {"engine": "orca", "functional": "r2SCAN-3c", "recalc_hess": val}
                }
            },
            schema,
        )
        assert not errors, f"val={val} → {errors}"
        expected = "auto" if val == "auto" else int(val) if isinstance(val, str) else val
        assert levels["optimize"]["recalc_hess"] == expected
    # Invalid values surface as errors.
    for bad in ("fast", -1, 1001, 1.5, True):
        _, errors = normalize_and_validate_method_config(
            {
                "levels": {
                    "optimize": {"engine": "orca", "functional": "r2SCAN-3c", "recalc_hess": bad}
                }
            },
            schema,
        )
        assert errors, f"val={bad} should have produced an error"
        assert "recalc_hess" in errors[0]


def test_recalc_hess_default_when_omitted() -> None:
    """When the user omits recalc_hess, the catalog default ('auto') applies."""
    schema = {
        "method_levels": [
            {
                "level_id": "optimize",
                "allowed_engines": ["orca"],
                "fields": ["functional", "recalc_hess"],
                "required": True,
            }
        ]
    }
    levels, errors = normalize_and_validate_method_config(
        {"levels": {"optimize": {"engine": "orca", "functional": "r2SCAN-3c"}}},
        schema,
    )
    assert not errors
    assert levels["optimize"]["recalc_hess"] == "auto"


def test_method_levels_to_cli_flags_recalc_hess() -> None:
    """recalc_hess maps to --calc-hess / --no-calc-hess, not --recalc-hess."""
    from acp.catalog import _LEVEL_TO_CLI_FLAG_MAP, method_levels_to_cli_flags

    assert "recalc_hess" not in _LEVEL_TO_CLI_FLAG_MAP
    assert method_levels_to_cli_flags({"optimize": {"engine": "orca", "recalc_hess": "auto"}}) == [
        "--calc-hess",
        "auto",
    ]
    assert method_levels_to_cli_flags({"optimize": {"engine": "orca", "recalc_hess": 0}}) == [
        "--no-calc-hess"
    ]
    assert method_levels_to_cli_flags({"optimize": {"engine": "orca", "recalc_hess": 7}}) == [
        "--calc-hess",
        "7",
    ]
    # Null / empty values are skipped.
    assert method_levels_to_cli_flags({"optimize": {"engine": "orca", "recalc_hess": None}}) == []
    assert method_levels_to_cli_flags({"optimize": {"engine": "orca", "recalc_hess": ""}}) == []


def test_method_levels_to_cli_flags_recalc_hess_skipped_on_prefix() -> None:
    """recalc_hess on a prefixed (sp-) level is skipped — SP does not optimise."""
    from acp.catalog import method_levels_to_cli_flags

    flags = method_levels_to_cli_flags(
        {
            "optfreq": {"engine": "orca", "recalc_hess": 7},
            "single_point": {"engine": "orca", "functional": "wB97M-V", "recalc_hess": 5},
        },
        {"optfreq": "", "single_point": "sp-", "thermo": ""},
    )
    assert flags == ["--calc-hess", "7", "--sp-method", "wB97M-V"]


def test_convert_method_levels_passes_recalc_hess() -> None:
    """convert_method_levels_to_protocol_levels routes recalc_hess into the
    protocol 'optimization' stage (plan §10.3 / AC12)."""
    out = convert_method_levels_to_protocol_levels(
        {"dft_opt": {"engine": "orca", "functional": "r2SCAN-3c", "recalc_hess": 5}}
    )
    assert out["optimization"]["recalc_hess"] == 5


# =====================================================================
# T11: GFN entries in METHOD_META + method-family-aware field gating
# =====================================================================

_GFN_METHOD_NAMES = ("GFN2-xTB", "GFN1-xTB", "GFN0-xTB", "GFN-FF")


def test_functional_options_map_gfn_basis_empty() -> None:
    from acp.catalog import _derive_functional_options_map

    derived = _derive_functional_options_map()
    for name in _GFN_METHOD_NAMES:
        assert derived[name]["basis"] == [], name
        assert FUNCTIONAL_OPTIONS_MAP[name]["basis"] == [], name
        assert derived[name]["dispersion"] == [], name


def test_method_meta_consistent_with_cccp_registry() -> None:
    from cccp.qc.keyword_registry import KeywordValueError, method_family, resolve_implementation

    for name, meta in METHOD_META.items():
        family = method_family(name)
        assert family != "unknown", (
            f"{name} classifies as 'unknown' in the cccp registry — extend "
            "cccp.qc.keyword_registry._METHOD_FAMILY_TABLE (+ its tests)"
        )
        assert meta["family"] == family, f"{name} family drifted from the registry"
        assert meta["implementation"] == resolve_implementation(name, engine="orca"), (
            f"{name} implementation drifted from the registry"
        )

    for name in ("GFN2-xTB", "GFN1-xTB", "GFN0-xTB"):
        assert METHOD_META[name]["family"] == "gfn"
    assert METHOD_META["GFN-FF"]["family"] == "gfnff"
    for name in _GFN_METHOD_NAMES:
        meta = METHOD_META[name]
        assert meta["solvent_models"] == ["none", "ALPB"], name
        assert meta["ri_support"] == "composite", name
        assert meta["basis"] == (), name
        assert meta["default_basis"] == ""
        assert meta["default_dispersion"] == "none"

    assert METHOD_META["GFN2-xTB"]["builtin_dispersion"] == "D4"
    assert METHOD_META["GFN1-xTB"]["builtin_dispersion"] == "D3"
    assert METHOD_META["GFN-FF"]["builtin_dispersion"] == "builtin"

    from cccp.qc.keyword_registry import resolve

    for name in _GFN_METHOD_NAMES:
        meta = METHOD_META[name]
        for model in ("GBSA", "CPCM", "SMD"):
            with pytest.raises(KeywordValueError):
                resolve(
                    "solvent_model",
                    model,
                    family=meta["family"],
                    implementation=meta["implementation"],
                )


# T17 contract: registry↔METHOD_META consistency lock in BOTH directions —
# catalog → registry is test_method_meta_consistent_with_cccp_registry above.
def test_cccp_registry_method_table_fully_covered_by_method_meta() -> None:
    """Every cccp-registry method is representable in METHOD_META.

    Checks the registry → catalog direction: family and implementation must
    agree, and every non-``unknown`` registry family must have at least one
    METHOD_META entry.
    """
    from cccp.qc.keyword_registry import (
        _METHOD_FAMILY_TABLE,
        FAMILIES,
        resolve_implementation,
    )

    meta_ci = {name.upper(): name for name in METHOD_META}
    # Registry classification keys that are alternate spellings of a catalog
    # display name — NOT case variants (deleting this map breaks the lock).
    registry_aliases = {"MPW1PW": "mPW1PW91", "GFNFF": "GFN-FF"}

    for method, family in sorted(_METHOD_FAMILY_TABLE.items()):
        catalog_name = registry_aliases.get(method) or meta_ci.get(method.upper())
        assert catalog_name is not None, (
            f"cccp registry method {method!r} has no METHOD_META entry — "
            "add it with family/implementation consistent with the registry"
        )
        meta = METHOD_META[catalog_name]
        assert meta["family"] == family, (method, catalog_name, meta["family"], family)
        assert meta["implementation"] == resolve_implementation(method, engine="orca"), method

    represented = {meta["family"] for meta in METHOD_META.values()}
    assert represented == FAMILIES - {"unknown"}


def test_gfn_field_options_locked_for_family() -> None:
    from acp.catalog import _resolve_field_default, _resolve_field_options

    for name in _GFN_METHOD_NAMES:
        assert _resolve_field_options("scan_optimizer_basis", "orca", name) == [], name
        assert _resolve_field_options("scan_optimizer_dispersion", "orca", name) == [], name
        assert _resolve_field_options("basis", "orca", name) == [], name
        assert _resolve_field_options("dispersion", "orca", name) == [], name
        assert _resolve_field_options("grid", "orca", name) == [], name
        assert _resolve_field_options("scf_convergence", "orca", name) == [], name
        assert _resolve_field_options("scan_optimizer_grid", "orca", name) == [], name
        assert _resolve_field_options("scan_optimizer_scf_convergence", "orca", name) == [], name
        assert _resolve_field_options("scan_optimizer_scf_max_iterations", "orca", name) is None, (
            name
        )
        assert _resolve_field_default("basis", "orca", name) == ""
        assert _resolve_field_default("dispersion", "orca", name) == "none"
        assert _resolve_field_default("ri_approximation", "orca", name) == "none"
        assert _resolve_field_default("scan_optimizer_basis", "orca", name) == ""
        assert _resolve_field_default("scan_optimizer_dispersion", "orca", name) == "none"
        assert _resolve_field_default("scan_optimizer_ri_approximation", "orca", name) == "none"

    assert _resolve_field_options("grid", "orca", "B3LYP") == FIELD_DEFINITIONS["grid"]["options"]
    assert (
        _resolve_field_options("scf_convergence", "orca", "B3LYP")
        == (FIELD_DEFINITIONS["scf_convergence"]["options"])
    )
    assert _resolve_field_options("scan_optimizer_basis", "orca", "r2SCAN-3c") == ["def2-mTZVPP"]
    assert (
        _resolve_field_options("scan_optimizer_grid", "orca", "B97-3c")
        == (FIELD_DEFINITIONS["scan_optimizer_grid"]["options"])
    )


def test_gfn_locked_fields_accept_cleared_form_only() -> None:
    from acp.catalog import get_method_schema, normalize_and_validate_method_config

    schema = get_method_schema("pes_scan")
    assert schema is not None

    profile_shaped = {
        "levels": {
            "scan_optimizer": {
                "engine": "orca",
                "scan_optimizer_method": "GFN2-xTB",
                "scan_optimizer_basis": "",
                "scan_optimizer_dispersion": "none",
                "scan_optimizer_grid": "",
                "scan_optimizer_scf_convergence": "",
                "scan_optimizer_scf_max_iterations": 200,
            },
            "single_point": {"engine": "orca", "_disabled": True},
        }
    }
    levels, errors = normalize_and_validate_method_config(profile_shaped, schema)
    assert errors == [], errors
    assert levels["scan_optimizer"]["scan_optimizer_dispersion"] == "none"
    assert levels["scan_optimizer"]["scan_optimizer_scf_max_iterations"] == 200

    override = {
        "levels": {
            "scan_optimizer": {
                "engine": "orca",
                "scan_optimizer_method": "GFN2-xTB",
                "scan_optimizer_basis": "def2-SVP",
                "scan_optimizer_dispersion": "D4",
                "scan_optimizer_grid": "DefGrid2",
                "scan_optimizer_scf_convergence": "tight",
            },
            "single_point": {"engine": "orca", "_disabled": True},
        }
    }
    _, errors = normalize_and_validate_method_config(override, schema)
    joined = " | ".join(errors)
    for field in (
        "scan_optimizer_basis",
        "scan_optimizer_dispersion",
        "scan_optimizer_grid",
        "scan_optimizer_scf_convergence",
    ):
        assert field in joined, (field, errors)
    assert "family-locked field" in joined


# =====================================================================
# T12: family-aware scan solvent options + ORCA-native grid
# =====================================================================

_LEGACY_GRID_NAMES = {"SG1", "Fine", "UltraFine", "SuperFine"}


def _iter_grid_defaults(node: object):
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("grid", "scan_optimizer_grid") and isinstance(value, str):
                yield value
            yield from _iter_grid_defaults(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_grid_defaults(item)


def test_grid_field_is_orca_native_with_no_legacy_names() -> None:
    from acp.catalog import FIELD_DEFINITIONS

    grid = FIELD_DEFINITIONS["grid"]
    assert grid["options"] == ["DefGrid1", "DefGrid2", "DefGrid3"]
    assert grid["default"]["*"] == ""
    assert set(grid["option_labels_zh"]) == {"DefGrid1", "DefGrid2", "DefGrid3"}

    catalog_grid_names = set(grid["options"]) | set(grid["option_labels_zh"])
    catalog_grid_names.add(grid["default"]["*"])
    assert not (catalog_grid_names & _LEGACY_GRID_NAMES), (
        "catalog grid field still exposes a legacy alias in options/default/option_labels_zh"
    )


def test_no_legacy_grid_default_anywhere_in_catalog() -> None:
    from acp.catalog import METHOD_SCHEMAS

    residual = {
        value for value in _iter_grid_defaults(METHOD_SCHEMAS) if value in _LEGACY_GRID_NAMES
    }
    assert residual == set(), residual


def test_scan_optimizer_solvent_model_is_family_aware() -> None:
    from acp.catalog import _resolve_field_options

    for name in _GFN_METHOD_NAMES:
        assert _resolve_field_options("scan_optimizer_solvent_model", "orca", name) == [
            "none",
            "ALPB",
        ], name

    for dft in ("r2SCAN-3c", "B97-3c", "B3LYP", "wB97M-V"):
        assert _resolve_field_options("scan_optimizer_solvent_model", "orca", dft) == [
            "none",
            "CPCM",
            "SMD",
        ], dft


def test_legacy_grid_value_migrates_to_defgrid_with_warning(caplog) -> None:
    from acp.catalog import _resolve_field_default, get_method_schema

    schema = get_method_schema("pes_scan")
    assert schema is not None

    config = {
        "levels": {
            "scan_optimizer": {
                "engine": "orca",
                "scan_optimizer_method": "r2SCAN-3c",
                "scan_optimizer_grid": "UltraFine",
            },
            "single_point": {"engine": "orca", "_disabled": True},
        }
    }
    with caplog.at_level("WARNING", logger="acp.catalog"):
        levels, errors = normalize_and_validate_method_config(config, schema)

    assert errors == [], errors
    assert levels["scan_optimizer"]["scan_optimizer_grid"] == "DefGrid3"
    assert any("UltraFine" in rec.message and "DefGrid3" in rec.message for rec in caplog.records)

    assert _resolve_field_default("scan_optimizer_grid", "orca", "r2SCAN-3c") == ""
    assert _resolve_field_default("grid", "orca", "B3LYP") == ""


def test_legacy_grid_alias_registry_roundtrip() -> None:
    from cccp.qc.keyword_registry import resolve

    canonical, warning = resolve(
        "grid", "SuperFine", family="conventional_dft", implementation="orca_dft"
    )
    assert canonical == "DefGrid3"
    assert warning and "legacy alias" in warning


# =====================================================================
# Catalog-parity sweep (plan todo 7, moved from tests/test_cccp_keyword_registry.py)
# =====================================================================

# Every enum value declared in acp.catalog (METHOD_META, METHOD_SCHEMAS-
# referenced fields, and the FIELD_DEFINITIONS option tables) must resolve
# in the cccp keyword registry — including the ``normal``/``none`` no-ops.
# This parity lives on the ACP side because it is the only side allowed to
# import acp (tests/test_cccp_keyword_registry.py never imports acp).

# Catalog field name -> registry enum domain.  Covers every enumerated
# keyword field the catalog declares (METHOD_SCHEMAS references these by
# name; FIELD_DEFINITIONS carries the option lists).
_FIELD_TO_DOMAIN: dict[str, str] = {
    "opt_level": "opt_level",
    "opt_convergence": "opt_level",
    "scf_convergence": "scf_convergence",
    "scan_optimizer_scf_convergence": "scf_convergence",
    "minimum_scf_convergence": "scf_convergence",
    "scf_strategy": "scf_strategy",
    "minimum_scf_strategy": "scf_strategy",
    "transition_state_scf_strategy": "scf_strategy",
    "grid": "grid",
    "scan_optimizer_grid": "grid",
    "dispersion": "dispersion",
    "scan_optimizer_dispersion": "dispersion",
}


def _method_schema_referenced_fields() -> set[str]:
    """Field names referenced by METHOD_SCHEMAS (``fields`` / ``inherits``)."""
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "fields" and isinstance(value, list):
                    found.update(str(item) for item in value)
                elif key == "inherits" and isinstance(value, str):
                    found.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(METHOD_SCHEMAS)
    return found


def _harvest_catalog_values() -> dict[str, dict[str, set[str]]]:
    """Return ``domain -> scope -> values`` declared in acp.catalog.

    Scopes: ``"global"`` values are declared without backend restriction and
    must resolve under every (family, implementation) context; ``"xtb"`` /
    ``"orca"`` values are declared for one backend and resolve under that
    backend's implementations.
    """
    out: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    # METHOD_META: per-method dispersion tuples (global declarations).
    for meta in METHOD_META.values():
        for value in meta.get("dispersion") or ():
            out["dispersion"]["global"].add(str(value))

    # METHOD_SCHEMAS-referenced fields + every catalog enum field.
    field_names = (_method_schema_referenced_fields() | set(_FIELD_TO_DOMAIN)) & set(
        _FIELD_TO_DOMAIN
    )
    for name in sorted(field_names):
        domain = _FIELD_TO_DOMAIN[name]
        field_def = FIELD_DEFINITIONS.get(name) or {}
        for value in field_def.get("options") or ():
            out[domain]["global"].add(str(value))
        for backend, values in (field_def.get("per_backend") or {}).items():
            if isinstance(values, list):
                for value in values:
                    out[domain][str(backend)].add(str(value))

    # METHOD_SCHEMAS may also carry inline option lists (future-proof).
    def walk_options(node: Any, key: str | None = None) -> None:
        if isinstance(node, dict):
            if key in _FIELD_TO_DOMAIN and isinstance(node.get("options"), list):
                for value in node["options"]:
                    out[_FIELD_TO_DOMAIN[key]]["global"].add(str(value))
            for child_key, value in node.items():
                walk_options(value, child_key)
        elif isinstance(node, list):
            for item in node:
                walk_options(item, key)

    walk_options(METHOD_SCHEMAS)
    return out


def test_every_catalog_declared_enum_value_resolves() -> None:
    from cccp.qc.keyword_registry import (
        ENUM_DOMAINS,
        IMPL_ORCA_DFT,
        IMPL_ORCA_EXTERNAL_XTB,
        IMPL_ORCA_NATIVE,
        IMPL_XTB_BINARY,
        resolve,
    )

    all_contexts: list[tuple[str, str]] = [
        ("conventional_dft", IMPL_ORCA_DFT),
        ("composite_3c", IMPL_ORCA_DFT),
        ("gfn", IMPL_ORCA_EXTERNAL_XTB),
        ("gfn", IMPL_XTB_BINARY),
        ("gfnff", IMPL_ORCA_NATIVE),
    ]
    harvested = _harvest_catalog_values()
    # Guard against a silent harvest regression (values keep catalog spelling).
    assert {"Loose", "Normal", "Tight", "VeryTight"} <= harvested["opt_level"]["global"]
    assert "crude" in harvested["opt_level"]["xtb"]
    assert "normal" in harvested["scf_strategy"]["global"]
    assert {"DefGrid1", "DefGrid2", "DefGrid3"} <= harvested["grid"]["global"]
    assert {"none", "D3", "D3BJ", "D4", "VV10"} <= harvested["dispersion"]["global"]

    for domain, scopes in harvested.items():
        assert domain in ENUM_DOMAINS, domain
        for scope, values in scopes.items():
            if scope == "global":
                contexts = all_contexts
            elif scope == "xtb":
                contexts = [("gfn", IMPL_XTB_BINARY)]
            else:
                contexts = [("conventional_dft", IMPL_ORCA_DFT)]
            for value in sorted(values):
                for family, implementation in contexts:
                    canonical, warning = resolve(
                        domain, value, family=family, implementation=implementation
                    )
                    key = value.strip().lower()
                    if canonical is None:
                        # no-op where applicable: engine default (normal/none),
                        # a stripped field, or a value with no token on this
                        # implementation — all of which must warn unless they
                        # are true no-ops.
                        assert key in {"normal", "none"} or warning is not None, (
                            domain,
                            value,
                            family,
                            implementation,
                        )


# --- todo 25: nmr resolver-parameter field help (no schema changes) --------
def test_nmr_resolver_parameter_field_help_present() -> None:
    """functional/basis/solvent_model/ewin/max_conformers carry help text."""
    field_defs = get_method_catalog()["field_definitions"]
    for name in ("functional", "basis", "solvent_model", "ewin", "max_conformers"):
        assert field_defs[name].get("help"), name
        assert field_defs[name].get("help_zh"), name
    assert field_defs["max_conformers"]["default"]["*"] == 10


def test_nmr_schema_levels_unchanged_by_todo25() -> None:
    """Help is FIELD_DEFINITIONS-only: nmr method-schema levels untouched."""
    schema = METHOD_SCHEMAS["nmr"]
    fields_by_level = {
        level["level_id"]: list(level["fields"]) for level in schema["method_levels"]
    }
    assert fields_by_level["conformer"] == ["ewin", "refinement_threshold"]
    assert fields_by_level["giaoa"] == [
        "functional",
        "basis",
        "solvent_model",
        "solvent",
        "nuclei",
        "boltzmann_temp",
        "tms_shielding_h",
        "tms_shielding_c",
    ]
    assert "max_conformers" not in fields_by_level["conformer"]
    assert [p["profile_id"] for p in schema["profiles"]] == ["nmr-goodman"]
