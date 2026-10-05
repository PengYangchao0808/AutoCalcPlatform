"""Matrix tests for the single typed NMR method resolver (todo 17 / gap G06).

Covers: default Goodman profile, custom functional/basis, ``solvent_model=none``
preservation, gas-phase config, nuclei/TMS/ewin/max_conformers overrides,
frontend-hoist independence, and typed rejection of method/model mismatches.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, fields

import pytest

from acp.nmr.method_config import (
    NmrMethodConfig,
    NmrMethodConfigError,
    resolve_nmr_method,
)

# Frontend wizard payload shape (frontend/ACP_Workbench_v2.html ~30776) —
# WITHOUT the hoisted top-level keys, proving the resolver reads levels.giaoa.
_GOODMAN_PROFILE = {
    "schema_id": "nmr",
    "profile_id": "nmr-goodman",
    "preset": "nmr-goodman",
    "levels": {
        "conformer": {"engine": "censo", "ewin": 6.0, "refinement_threshold": 0.99},
        "giaoa": {
            "engine": "orca",
            "functional": "mPW1PW91",
            "basis": "6-311G(d)",
            "solvent_model": "cpcm",
            "solvent": "chloroform",
            "nuclei": ["1H", "13C"],
            "boltzmann_temp": 298.15,
        },
    },
}


def _hoisted_copy() -> dict:
    """Same payload with the frontend's hoist applied (top-level copies)."""
    payload = json.loads(json.dumps(_GOODMAN_PROFILE))
    giaoa = payload["levels"]["giaoa"]
    for key in (
        "nuclei",
        "boltzmann_temp",
        "tms_shielding_h",
        "tms_shielding_c",
        "functional",
        "basis",
    ):
        if giaoa.get(key) not in (None, ""):
            payload[key] = giaoa[key]
    if giaoa.get("solvent") and str(giaoa.get("solvent_model") or "cpcm").lower() != "none":
        payload["solvent"] = giaoa["solvent"]
    return payload


# ── defaults / matrix ─────────────────────────────────────────────────────


def test_default_profile_resolves_goodman_level() -> None:
    cfg = resolve_nmr_method(_GOODMAN_PROFILE, None)
    assert cfg == NmrMethodConfig(
        nmr_method="mPW1PW91",
        nmr_basis="6-311G(d)",
        solvent_model="cpcm",
        solvent="chloroform",
        nuclei=("1H", "13C"),
        boltzmann_temp=298.15,
        tms_1h=None,  # workflow does the solvent-aware Goodman TMSdata lookup
        tms_13c=None,
        ewin=6.0,
        max_conformers=10,
        error_model="goodman-legacy",
        conformer_preset="censo-light",  # "nmr-goodman" is not a CENSO preset
    )


def test_empty_payload_and_no_config_match_workflow_defaults() -> None:
    assert resolve_nmr_method({}, None) == resolve_nmr_method(_GOODMAN_PROFILE, None)


def test_frontend_hoist_is_not_required() -> None:
    """Same effective config with and without the frontend's top-level hoist."""
    assert resolve_nmr_method(_GOODMAN_PROFILE, None) == resolve_nmr_method(_hoisted_copy(), None)


def test_custom_functional_and_basis_flat_payload() -> None:
    cfg = resolve_nmr_method(
        {"functional": "B3LYP", "basis": "def2-SVP", "solvent_model": "SMD", "solvent": "water"},
        None,
    )
    assert cfg.nmr_method == "B3LYP"
    assert cfg.nmr_basis == "def2-SVP"
    assert cfg.solvent_model == "smd"  # case-normalised like the catalog
    assert cfg.solvent == "water"
    # untouched keys keep the defaults
    assert cfg.nuclei == ("1H", "13C")
    assert cfg.error_model == "goodman-legacy"
    assert cfg.conformer_preset == "censo-light"


def test_custom_functional_from_giaoa_level_only() -> None:
    method = json.loads(json.dumps(_GOODMAN_PROFILE))
    method["levels"]["giaoa"]["functional"] = "PBE0"
    method["levels"]["giaoa"]["basis"] = "def2-TZVP"
    cfg = resolve_nmr_method(method, None)
    assert (cfg.nmr_method, cfg.nmr_basis) == ("PBE0", "def2-TZVP")


def test_legacy_nmr_method_keys_take_precedence_over_aliases() -> None:
    cfg = resolve_nmr_method(
        {
            "nmr_method": "B3LYP",
            "functional": "mPW1PW91",
            "nmr_basis": "def2-SVP",
            "basis": "6-311G(d)",
        },
        None,
    )
    assert (cfg.nmr_method, cfg.nmr_basis) == ("B3LYP", "def2-SVP")


def test_solvent_model_none_from_level_is_preserved() -> None:
    """G06 core: an explicit 'none' never becomes cpcm/chloroform."""
    method = json.loads(json.dumps(_GOODMAN_PROFILE))
    method["levels"]["giaoa"]["solvent_model"] = "none"
    method["levels"]["giaoa"]["solvent"] = ""
    cfg = resolve_nmr_method(
        method,
        {"theory": {"nmr": {"solvent_model": "cpcm", "solvent": "chloroform"}}},
    )
    assert cfg.solvent_model == "none"
    assert cfg.solvent == ""  # gas phase — no default chloroform


def test_solvent_model_none_string_variant_is_normalised() -> None:
    cfg = resolve_nmr_method({"solvent_model": "None", "solvent": "chloroform"}, None)
    assert cfg.solvent_model == "none"
    assert cfg.solvent == ""


def test_gas_phase_config_stays_gas_phase() -> None:
    cfg = resolve_nmr_method(
        {},
        {"theory": {"nmr": {"solvent_model": "none", "solvent": "water"}}},
    )
    assert cfg.solvent_model == "none"
    assert cfg.solvent == ""  # config solvent ignored — gas phase wins


def test_config_provides_method_basis_and_temperature() -> None:
    cfg = resolve_nmr_method(
        {},
        {
            "theory": {"nmr": {"method": "B3LYP", "basis": "def2-TZVP"}},
            "nmr": {"temperature_k": 350.0},
            "censo": {"ewin": 4.0},
        },
    )
    assert (cfg.nmr_method, cfg.nmr_basis) == ("B3LYP", "def2-TZVP")
    assert cfg.boltzmann_temp == 350.0
    assert cfg.ewin == 4.0


# ── overrides ─────────────────────────────────────────────────────────────


def test_nuclei_tms_ewin_max_conformers_overrides() -> None:
    cfg = resolve_nmr_method(
        {
            "nuclei": ["1H", "13C", "15N"],
            "tms_shielding_h": 31.5,
            "tms_shielding_c": 188.9,
            "ewin": 4.5,
            "max_conformers": 5,
            "error_model": "placeholder-student-t",
            "conformer_preset": "censo-default",
        },
        None,
    )
    assert cfg.nuclei == ("1H", "13C", "15N")
    assert cfg.tms_1h == 31.5
    assert cfg.tms_13c == 188.9
    assert cfg.ewin == 4.5
    assert cfg.max_conformers == 5
    assert cfg.error_model == "placeholder-student-t"
    assert cfg.conformer_preset == "censo-default"


def test_nuclei_scalar_string_and_case_canonicalisation() -> None:
    cfg = resolve_nmr_method({"nuclei": "1h,13c"}, None)
    assert cfg.nuclei == ("1H", "13C")


def test_ewin_reads_conformer_level_not_censo_level() -> None:
    """G06: the NMR conformer level id is ``conformer`` (not confsearch's ``censo``)."""
    method = json.loads(json.dumps(_GOODMAN_PROFILE))
    method["levels"]["conformer"]["ewin"] = 3.5
    cfg = resolve_nmr_method(method, {"censo": {"ewin": 9.0}})
    assert cfg.ewin == 3.5  # level beats config


def test_max_conformers_from_config() -> None:
    cfg = resolve_nmr_method({}, {"nmr": {"max_conformers": 7}})
    assert cfg.max_conformers == 7


def test_non_positive_values_fall_through_to_defaults() -> None:
    cfg = resolve_nmr_method({"ewin": 0, "boltzmann_temp": 0, "max_conformers": 0}, None)
    assert cfg.ewin == 6.0
    assert cfg.boltzmann_temp == 298.15
    assert cfg.max_conformers == 10


# ── typed rejections ──────────────────────────────────────────────────────


def test_reject_unknown_functional() -> None:
    with pytest.raises(NmrMethodConfigError, match="METHOD_META"):
        resolve_nmr_method({"functional": "NotAFunctional"}, None)


def test_reject_gfn_functional_by_platform_nmr_policy() -> None:
    with pytest.raises(NmrMethodConfigError, match="GFN\\+NMR"):
        resolve_nmr_method({"functional": "GFN2-xTB"}, None)


def test_reject_fixed_basis_pair_conflict() -> None:
    with pytest.raises(NmrMethodConfigError, match="functional/basis pair rejected"):
        resolve_nmr_method({"functional": "r2SCAN-3c", "basis": "def2-SVP"}, None)


def test_fixed_basis_method_without_basis_clamps_to_declared_basis() -> None:
    cfg = resolve_nmr_method({"functional": "r2SCAN-3c"}, None)
    assert cfg.nmr_method == "r2SCAN-3c"
    assert cfg.nmr_basis == "def2-mTZVPP"


def test_reject_schema_id_of_another_workflow() -> None:
    with pytest.raises(NmrMethodConfigError, match="schema_id"):
        resolve_nmr_method({"schema_id": "confsearch", "functional": "mPW1PW91"}, None)


def test_reject_levels_without_giaoa_level() -> None:
    with pytest.raises(NmrMethodConfigError, match="giaoa"):
        resolve_nmr_method({"levels": {"dft_opt": {"engine": "orca"}}}, None)


def test_reject_unknown_solvent_model() -> None:
    with pytest.raises(NmrMethodConfigError, match="solvent_model"):
        resolve_nmr_method({"solvent_model": "alpb"}, None)


def test_reject_non_numeric_temperature() -> None:
    with pytest.raises(NmrMethodConfigError, match="not numeric"):
        resolve_nmr_method({"boltzmann_temp": "warm"}, None)


def test_rejection_is_a_value_error() -> None:
    assert issubclass(NmrMethodConfigError, ValueError)


def test_rejection_aggregates_all_problems() -> None:
    with pytest.raises(NmrMethodConfigError) as excinfo:
        resolve_nmr_method(
            {"schema_id": "confsearch", "functional": "NotAFunctional", "solvent_model": "alpb"},
            None,
        )
    message = str(excinfo.value)
    assert "schema_id" in message and "METHOD_META" in message and "solvent_model" in message


# ── contract shape ────────────────────────────────────────────────────────


def test_config_is_frozen() -> None:
    cfg = resolve_nmr_method({}, None)
    with pytest.raises(FrozenInstanceError):
        cfg.nmr_method = "B3LYP"  # type: ignore[misc]


def test_every_field_round_trips_through_to_dict() -> None:
    cfg = resolve_nmr_method(_hoisted_copy(), None)
    payload = cfg.to_dict()
    assert set(payload) == {f.name for f in fields(NmrMethodConfig)}
    assert json.loads(json.dumps(payload)) == payload  # JSON-safe provenance
    assert NmrMethodConfig(**payload) == cfg


def test_custom_matrix_case_round_trips_through_to_dict() -> None:
    cfg = resolve_nmr_method(
        {
            "functional": "B3LYP",
            "basis": "def2-SVP",
            "solvent_model": "none",
            "nuclei": ["13C"],
            "ewin": 2.5,
            "max_conformers": 3,
            "error_model": "placeholder-student-t",
            "preset": "censo-zero",
        },
        None,
    )
    assert NmrMethodConfig(**cfg.to_dict()) == cfg
    assert cfg.solvent_model == "none" and cfg.solvent == ""
