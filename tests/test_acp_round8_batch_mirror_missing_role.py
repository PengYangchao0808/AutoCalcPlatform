"""Round-8 #4: every top-level Batch mirror must land in the executed role.

``_WORKBENCH_FLAT_MIRRORS`` (top-level ``optimization_*``/``single_point_*`` and
the thermo triples) is inert when ``batch_roles`` is present — the engine reads
the roles.  Before round 8 only the *single-point* mirrors were refused when the
target ``int`` role/field was missing; the others fell through the value loop and
were **silently dropped** (``role_value`` is not a mapping → ``continue``).

These tests pin the extension:

* a top-level mirror without its ``batch_roles.<role>.<field>`` target is refused
  with ``rule=missing_role_mirror`` and ``details.role`` naming the exact target;
* the default thermo values (``298.15``/``1.0``/``0.9905``) stay inert (the
  Workbench UI keeps them next to user-set role values);
* the value-mismatch branch stays a separate ``shadowed_field`` refusal;
* a fully matching mirror payload still passes and the effective levels are
  unchanged.
"""

from __future__ import annotations

from collections.abc import Generator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.api.submission_contract import SubmissionContractError, validate_workflow_submission
from acp.calculations.submission_validation import analysis_for

_XYZ = "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n"
_INPUT: dict[str, Any] = {"source_type": "batch_structures", "items": [{"xyz": _XYZ}]}

_TS_ONLY = {"method": "wB97X-D4", "basis": "def2-TZVP"}


def _validate(method: Mapping[str, Any]) -> None:
    validate_workflow_submission("BatchOptimize", input=_INPUT, method=method, resources={})


def _contract_error(method: Mapping[str, Any]) -> SubmissionContractError:
    with pytest.raises(SubmissionContractError) as caught:
        _validate(method)
    return caught.value


# ── missing role / missing role field ───────────────────────────────────


def test_ts_only_optimization_method_is_refused() -> None:
    error = _contract_error(
        {
            "profile": "opt_freq",
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "optimization_method": "wB97X-D4",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.field_name == "method.optimization_method"
    params = error.params()
    assert params["role"] == "batch_roles.int.method"
    assert params["role_field"] == "method"
    assert params["role_present"] is False
    assert "batch_roles.int.method" in str(error)


def test_ts_only_optimization_basis_is_refused() -> None:
    error = _contract_error(
        {
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "optimization_basis": "def2-TZVP",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.params()["role"] == "batch_roles.int.basis"


def test_int_role_without_the_mirrored_field_is_refused() -> None:
    """The role exists but has no ``method``: the flat value is still inert."""
    error = _contract_error(
        {
            "batch_roles": {"int": {"basis": "def2-SVP"}, "ts": dict(_TS_ONLY)},
            "optimization_method": "r2SCAN-3c",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.params()["role"] == "batch_roles.int.method"
    assert error.params()["role_present"] is True


def test_non_default_temperature_without_an_int_role_is_refused() -> None:
    error = _contract_error(
        {
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "temperature": 350.0,
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.field_name == "method.temperature"
    assert error.params()["role"] == "batch_roles.int.temperature"
    assert error.params()["flat_value"] == 350.0


def test_non_default_pressure_and_scale_factor_are_checked() -> None:
    for key, value in (("pressure", 2.0), ("scale_factor", 0.98)):
        error = _contract_error({"batch_roles": {"ts": dict(_TS_ONLY)}, key: value})
        assert error.rule == "missing_role_mirror", key
        assert error.params()["role"] == f"batch_roles.int.{key}", key


def test_default_thermo_values_stay_inert() -> None:
    """The Workbench sends the defaults next to the roles; they must pass."""
    _validate(
        {
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "temperature": 298.15,
            "pressure": 1.0,
            "scale_factor": 0.9905,
        }
    )


def test_empty_mirror_values_stay_inert() -> None:
    _validate(
        {
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "optimization_method": "",
            "temperature": None,
        }
    )


def test_single_point_mirror_rule_is_unchanged() -> None:
    error = _contract_error(
        {
            "batch_roles": {"ts": dict(_TS_ONLY)},
            "single_point_method": "DLPNO-CCSD(T)",
        }
    )
    assert error.rule == "missing_role_mirror"
    assert error.params()["role"] == "batch_roles.int.sp_method"


# ── matching / conflicting values ──────────────────────────────────────


def test_full_matching_mirror_payload_passes() -> None:
    _validate(
        {
            "profile": "opt_freq_sp",
            "batch_roles": {
                "int": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
                "ts": dict(_TS_ONLY),
            },
            "optimization_method": "r2SCAN-3c",
            "optimization_basis": "def2-SVP",
            "temperature": 298.15,
        }
    )


def test_conflicting_mirror_value_keeps_the_separate_rule() -> None:
    error = _contract_error(
        {
            "batch_roles": {"int": {"method": "r2SCAN-3c"}, "ts": dict(_TS_ONLY)},
            "optimization_method": "B3LYP",
        }
    )
    assert error.rule == "shadowed_field"
    assert error.field_name == "method.optimization_method"
    assert error.params()["role"] == "batch_roles.int.method"
    assert error.params()["role_value"] == "r2SCAN-3c"
    assert error.params()["flat_value"] == "B3LYP"


def test_conflicting_thermo_value_keeps_the_separate_rule() -> None:
    error = _contract_error(
        {
            "batch_roles": {
                "int": {"method": "r2SCAN-3c", "temperature": 350.0},
                "ts": dict(_TS_ONLY),
            },
            "temperature": 333.15,
        }
    )
    assert error.rule == "shadowed_field"
    assert error.params()["role"] == "batch_roles.int.temperature"


def test_effective_levels_are_unchanged_by_the_mirror_check() -> None:
    method = {
        "profile": "opt_freq",
        "batch_roles": {
            "int": {"method": "r2SCAN-3c", "basis": "def2-SVP"},
            "ts": dict(_TS_ONLY),
        },
        "optimization_method": "r2SCAN-3c",
        "optimization_basis": "def2-SVP",
    }
    analysis = analysis_for("BatchOptimize", _INPUT, method)
    assert analysis is not None
    roles = analysis.effective["roles"]
    assert roles["int"]["optimization_method"] == "r2SCAN-3c"
    assert roles["int"]["optimization_basis"] == "def2-SVP"
    assert roles["ts"]["optimization_method"] == "wB97X-D4"
    assert roles["ts"]["optimization_basis"] == "def2-TZVP"


# ── HTTP: the extended refusal agrees between preflight and submit ─────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    from acp.api.server import create_app

    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _post(client: TestClient, path: str, method: dict):
    return client.post(
        "/api/v1/preflight" if path == "preflight" else "/api/v1/jobs",
        json={"workflow": "BatchOptimize", "input": _INPUT, "method": method},
    )


def test_preflight_and_submit_refuse_the_extended_mirror_identically(client: TestClient) -> None:
    method = {
        "profile": "opt_freq",
        "batch_roles": {"ts": dict(_TS_ONLY)},
        "optimization_method": "wB97X-D4",
    }
    preflight = _post(client, "preflight", method)
    submit = _post(client, "submit", method)
    assert preflight.status_code == submit.status_code == 422, (
        preflight.status_code,
        preflight.text,
        submit.status_code,
        submit.text,
    )
    p = preflight.json()["detail"]
    s = submit.json()["detail"]
    assert p["code"] == s["code"] == "input_invalid"
    assert p["params"] == s["params"]
    assert p["params"]["rule"] == "missing_role_mirror"
    assert p["params"]["field"] == "method.optimization_method"
    assert p["params"]["role"] == "batch_roles.int.method"


def test_preflight_refusal_does_not_create_a_job(client: TestClient, tmp_path: Path) -> None:
    store = client.app.state.job_manager.store
    before = dict(store.counts())
    before_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    response = _post(
        client,
        "preflight",
        {"batch_roles": {"ts": dict(_TS_ONLY)}, "temperature": 350.0},
    )
    assert response.status_code == 422
    assert dict(store.counts()) == before
    after_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert after_tree == before_tree
