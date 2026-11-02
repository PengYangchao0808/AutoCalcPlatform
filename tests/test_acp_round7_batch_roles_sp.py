"""Round-7 #2: per-role single-point level for BatchOptimize (batch_roles).

The agent's report: with ``method.batch_roles`` present, a top-level
``single_point_method`` was neither applied nor refused, and a role's
``sp_method`` was dropped (only the INT role's SP level reached execution).

These tests pin the round-7 decisions:

* ``int``/``ts`` each resolve their own ``sp_method``/``sp_basis``;
* a role without an SP level executes its **own** optimization level — the
  other role's SP level is never applied to it;
* the legacy flat payload (no ``batch_roles``) keeps its global SP semantics;
* ``effective_config.json`` shows the per-role resolved SP level + provenance;
* a top-level SP mirror without a matching role field is refused with
  ``rule=missing_role_mirror`` (preflight and submit share this contract).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from acp.api.submission_contract import SubmissionContractError, validate_workflow_submission
from acp.calculations.batch.effective_config import build_batch_effective_config
from acp.calculations.batch.options import BatchMethodOptions
from acp.calculations.contracts import StepKind

_INPUT: dict[str, Any] = {
    "source_type": "batch_structures",
    "items": [{"xyz": "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n"}],
}


def _validate(method: Mapping[str, Any]) -> None:
    validate_workflow_submission("BatchOptimize", input=_INPUT, method=method, resources={})


def _contract_error(method: Mapping[str, Any]) -> SubmissionContractError:
    with pytest.raises(SubmissionContractError) as caught:
        _validate(method)
    return caught.value


# ── option resolution ───────────────────────────────────────────────────


def test_each_role_resolves_its_own_sp_level() -> None:
    opts = BatchMethodOptions.from_method_dict(
        {
            "batch_roles": {
                "int": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)",
                    "sp_basis": "def2-TZVPP",
                },
                "ts": {
                    "method": "wB97X-D4",
                    "basis": "def2-TZVP",
                    "sp_method": "r2SCAN-3c",
                    "sp_basis": "def2-TZVPPD",
                },
            }
        }
    )
    assert opts.for_step(StepKind.SINGLEPOINT, False) == ("DLPNO-CCSD(T)", "def2-TZVPP")
    assert opts.for_step(StepKind.SINGLEPOINT, True) == ("r2SCAN-3c", "def2-TZVPPD")
    assert opts.resolve_for_step(StepKind.SINGLEPOINT, False)["method"] == "DLPNO-CCSD(T)"
    assert opts.resolve_for_step(StepKind.SINGLEPOINT, True)["method"] == "r2SCAN-3c"
    # Optimization/frequency keep the role's optimization level.
    assert opts.for_step(StepKind.OPTIMIZE, False) == ("B3LYP", "def2-SVP")
    assert opts.for_step(StepKind.FREQUENCY, True) == ("wB97X-D4", "def2-TZVP")


def test_ts_without_sp_level_never_inherits_the_int_sp_level() -> None:
    opts = BatchMethodOptions.from_method_dict(
        {
            "batch_roles": {
                "int": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)",
                    "sp_basis": "def2-TZVPP",
                },
                "ts": {"method": "wB97X-D4", "basis": "def2-TZVP"},
            }
        }
    )
    assert opts.for_step(StepKind.SINGLEPOINT, True) == ("wB97X-D4", "def2-TZVP")
    assert opts.sp_provenance(True) == {"method": "role_method", "basis": "role_basis"}
    assert opts.sp_provenance(False) == {"method": "role", "basis": "role"}


def test_legacy_flat_payload_keeps_the_global_sp_for_both_roles() -> None:
    """No ``batch_roles`` → the historical single value applies to both roles."""
    opts = BatchMethodOptions.from_method_dict(
        {
            "optimization_method": "B3LYP",
            "optimization_basis": "def2-SVP",
            "single_point_method": "wB97M-V",
            "single_point_basis": "def2-TZVPP",
        }
    )
    assert opts.for_step(StepKind.SINGLEPOINT, False) == ("wB97M-V", "def2-TZVPP")
    assert opts.for_step(StepKind.SINGLEPOINT, True) == ("wB97M-V", "def2-TZVPP")
    assert opts.single_point_scope == "global"
    assert opts.sp_provenance(False) == {
        "method": "flat_single_point",
        "basis": "flat_single_point",
    }


def test_role_sp_change_changes_the_cache_key() -> None:
    base = {
        "batch_roles": {
            "int": {"method": "B3LYP", "basis": "def2-SVP"},
            "ts": {"method": "wB97X-D4", "basis": "def2-TZVP"},
        }
    }
    with_sp = {
        "batch_roles": {
            "int": {
                "method": "B3LYP",
                "basis": "def2-SVP",
                "sp_method": "DLPNO-CCSD(T)",
            },
            "ts": {"method": "wB97X-D4", "basis": "def2-TZVP"},
        }
    }
    assert (
        BatchMethodOptions.from_method_dict(base).cache_key
        != BatchMethodOptions.from_method_dict(with_sp).cache_key
    )


# ── effective config provenance ─────────────────────────────────────────


def test_effective_config_shows_the_per_role_single_point_level() -> None:
    opts = BatchMethodOptions.from_method_dict(
        {
            "profile": "opt_freq_sp",
            "batch_roles": {
                "int": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)",
                    "sp_basis": "def2-TZVPP",
                },
                "ts": {"method": "wB97X-D4", "basis": "def2-TZVP"},
            },
        }
    )
    config = build_batch_effective_config(opts)
    assert config["common"]["single_point_scope"] == "per_role"
    # No global SP value survives to be silently applied to both roles.
    assert config["common"]["single_point_method"] == ""
    assert config["common"]["single_point_basis"] == ""

    int_role = config["roles"]["int"]
    assert int_role["single_point_method"] == "DLPNO-CCSD(T)"
    assert int_role["single_point_basis"] == "def2-TZVPP"
    assert int_role["single_point_source"] == {"method": "role", "basis": "role"}
    assert int_role["method"] == "B3LYP"
    assert int_role["basis"] == "def2-SVP"
    assert int_role["opt_level"]

    ts_role = config["roles"]["ts"]
    assert ts_role["single_point_method"] == "wB97X-D4"
    assert ts_role["single_point_basis"] == "def2-TZVP"
    assert ts_role["single_point_source"] == {"method": "role_method", "basis": "role_basis"}


def test_legacy_effective_config_keeps_the_global_scope() -> None:
    opts = BatchMethodOptions.from_method_dict(
        {
            "optimization_method": "B3LYP",
            "optimization_basis": "def2-SVP",
            "single_point_method": "wB97M-V",
            "single_point_basis": "def2-TZVPP",
        }
    )
    config = build_batch_effective_config(opts)
    assert config["common"]["single_point_scope"] == "global"
    assert config["common"]["single_point_method"] == "wB97M-V"
    for role in ("int", "ts"):
        assert config["roles"][role]["single_point_method"] == "wB97M-V"
        assert config["roles"][role]["single_point_basis"] == "def2-TZVPP"


# ── submission contract: the flat SP mirror must land in a role ─────────


def test_sp_mirror_without_a_matching_role_is_refused() -> None:
    """The agent's payload: only a ``ts`` role + a top-level SP mirror."""
    error = _contract_error(
        {
            "profile": "opt_freq_sp",
            "batch_roles": {"ts": {"method": "wB97X-D4", "basis": "def2-TZVP"}},
            "single_point_method": "DLPNO-CCSD(T)",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.field_name == "method.single_point_method"
    params = error.params()
    assert params["role"] == "batch_roles.int.sp_method"
    assert params["role_present"] is False
    assert "batch_roles.int.sp_method" in str(error)


def test_sp_mirror_with_a_role_but_no_sp_field_is_refused() -> None:
    error = _contract_error(
        {
            "batch_roles": {
                "int": {"method": "B3LYP", "basis": "def2-SVP"},
                "ts": {"method": "wB97X-D4", "basis": "def2-TZVP"},
            },
            "single_point_basis": "def2-TZVPP",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.field_name == "method.single_point_basis"
    assert error.params()["role_present"] is True


def test_sp_mirror_matching_the_role_value_is_accepted() -> None:
    _validate(
        {
            "batch_roles": {
                "int": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)",
                }
            },
            "single_point_method": "DLPNO-CCSD(T)",
        }
    )


def test_sp_mirror_conflicting_with_the_role_value_is_refused() -> None:
    error = _contract_error(
        {
            "batch_roles": {
                "int": {
                    "method": "B3LYP",
                    "basis": "def2-SVP",
                    "sp_method": "DLPNO-CCSD(T)",
                }
            },
            "single_point_method": "wB97M-V",
        }
    )
    assert error.rule == "shadowed_field"
    assert error.field_name == "method.single_point_method"


def test_empty_sp_mirror_stays_inert() -> None:
    _validate(
        {
            "batch_roles": {"ts": {"method": "wB97X-D4", "basis": "def2-TZVP"}},
            "single_point_method": "",
            "single_point_basis": None,
        }
    )


def test_sp_method_alias_is_accepted_only_as_a_batch_roles_mirror() -> None:
    # With batch_roles: the alias mirrors batch_roles.int.sp_method.
    _validate(
        {
            "batch_roles": {"int": {"method": "B3LYP", "sp_method": "DLPNO-CCSD(T)"}},
            "sp_method": "DLPNO-CCSD(T)",
        }
    )
    # Without batch_roles there is no flat consumer for the alias → refuse,
    # never accept-and-drop.
    error = _contract_error({"sp_method": "DLPNO-CCSD(T)"})
    assert error.rule == "unknown_field"
    assert "single_point_method" in str(error)
