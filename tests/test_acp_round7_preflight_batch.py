"""Round-7 #6: read-only BatchOptimize / singlepoint preflight analysis.

``submission_analysis`` was always ``null`` for BatchOptimize and singlepoint
(``_ANALYZERS`` only covered PESsearch/scan/irc/tsmode), so preflight could not
show the effective levels and could not refuse the #2 silent-SP payload ahead
of a submit.

These tests pin:

* ``acp_preflight`` returns ``requested`` + ``effective`` for both workflows,
  with per-role single-point method/basis (round-7 #2) and the optimization
  level as rendered;
* the #2 ``missing_role_mirror`` refusal agrees between preflight and submit
  (same ``code``/``field``/``rule``);
* a basis conflict is refused by the read-only analysis (round-7 #3 helper);
* preflight stays zero-side-effect (no job rows, no task creation).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_XYZ = "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        yield test_client


def _preflight(client: TestClient, workflow: str, input_payload: dict, method: dict | None = None):
    return client.post(
        "/api/v1/preflight",
        json={"workflow": workflow, "input": input_payload, "method": method or {}},
    )


def _submit(client: TestClient, workflow: str, input_payload: dict, method: dict | None = None):
    return client.post(
        "/api/v1/jobs",
        json={"workflow": workflow, "input": input_payload, "method": method or {}},
    )


def _counts(client: TestClient) -> dict:
    return dict(client.app.state.job_manager.store.counts())


_BATCH_INPUT = {
    "source_type": "batch_structures",
    "items": [{"xyz": _XYZ}],
    "charge": 0,
    "multiplicity": 1,
}


def _batch_method() -> dict:
    return {
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


# ── effective projection ────────────────────────────────────────────────


def test_batch_preflight_exposes_per_role_levels(client: TestClient) -> None:
    response = _preflight(client, "BatchOptimize", _BATCH_INPUT, _batch_method())
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    assert receipt["executed"] is None
    assert receipt["requested"]["method"]["batch_roles"]["int"]["sp_method"] == "DLPNO-CCSD(T)"
    assert "batch_roles" in receipt["validated_scope"]
    assert "effective_levels" in receipt["validated_scope"]
    assert "route_basis" in receipt["validated_scope"]

    effective = receipt["effective"]
    assert effective["single_point_scope"] == "per_role"
    int_role = effective["roles"]["int"]
    assert int_role["optimization_method"] == "B3LYP"
    assert int_role["optimization_basis"] == "def2-SVP"
    assert int_role["optimization_level"] == "tight"
    assert int_role["orca_summary"]
    assert int_role["single_point_method"] == "DLPNO-CCSD(T)"
    assert int_role["single_point_basis"] == "def2-TZVPP"
    assert int_role["single_point_source"] == {"method": "role", "basis": "role"}

    ts_role = effective["roles"]["ts"]
    # The INT SP level must not appear on the TS role.
    assert ts_role["single_point_method"] == "wB97X-D4"
    assert ts_role["single_point_basis"] == "def2-TZVP"
    assert ts_role["single_point_source"] == {"method": "role_method", "basis": "role_basis"}

    # The inline item's role is projected (from its XYZ TAG comment).
    assert effective["items"] == [
        {
            "index": 0,
            "tag": "INT",
            "role": "int",
            "role_source": "xyz_tag",
            "role_value": "minimum",
        }
    ]
    assert all(role["route_conflicts"] == [] for role in effective["roles"].values())


def test_legacy_flat_batch_preflight_keeps_global_sp(client: TestClient) -> None:
    method = {
        "profile": "opt_freq_sp",
        "optimization_method": "B3LYP",
        "optimization_basis": "def2-SVP",
        "single_point_method": "wB97M-V",
        "single_point_basis": "def2-TZVPP",
    }
    response = _preflight(client, "BatchOptimize", _BATCH_INPUT, method)
    assert response.status_code == 200, response.text
    effective = response.json()["submission_analysis"]["effective"]
    assert effective["single_point_scope"] == "global"
    for role in ("int", "ts"):
        assert effective["roles"][role]["single_point_method"] == "wB97M-V"


def test_singlepoint_preflight_exposes_the_effective_level(client: TestClient) -> None:
    method = {
        "levels": {
            "single_point": {
                "functional": "wB97X-D4",
                "basis": "def2-TZVP",
                "dispersion": "D4",
            }
        }
    }
    response = _preflight(client, "singlepoint", {"input": "CCO"}, method)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    assert "single_point_level" in receipt["validated_scope"]
    effective = receipt["effective"]
    assert effective["method"] == "wB97X-D4"
    assert effective["basis"] == "def2-TZVP"
    assert effective["basis_source"] == "explicit_basis"
    assert effective["dispersion"] == "D4"


def test_singlepoint_without_a_level_marks_the_default_unverified(client: TestClient) -> None:
    response = _preflight(client, "singlepoint", {"input": "CCO"})
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    assert "default_level_resolution" in receipt["unverified_checks"]
    assert receipt["effective"]["basis_source"] == "profile_or_engine_default"


# ── static errors agree between preflight and submit ────────────────────


def _assert_parity(preflight, submit) -> None:
    assert preflight.status_code == submit.status_code == 422, (
        preflight.status_code,
        preflight.text,
        submit.status_code,
        submit.text,
    )
    p = preflight.json()["detail"]
    s = submit.json()["detail"]
    assert p["code"] == s["code"] == "input_invalid"
    assert (p.get("params") or {}).get("rule") == (s.get("params") or {}).get("rule")
    assert (p.get("params") or {}).get("field") == (s.get("params") or {}).get("field")


def test_missing_role_mirror_is_refused_identically(client: TestClient) -> None:
    method = {
        "profile": "opt_freq_sp",
        "batch_roles": {"ts": {"method": "wB97X-D4", "basis": "def2-TZVP"}},
        "single_point_method": "DLPNO-CCSD(T)",
    }
    preflight = _preflight(client, "BatchOptimize", _BATCH_INPUT, method)
    submit = _submit(client, "BatchOptimize", _BATCH_INPUT, method)
    _assert_parity(preflight, submit)
    params = preflight.json()["detail"]["params"]
    assert params["rule"] == "missing_role_mirror"
    assert params["field"] == "method.single_point_method"


def test_preflight_refusal_has_no_side_effects(client: TestClient) -> None:
    method = {
        "profile": "opt_freq_sp",
        "batch_roles": {"ts": {"method": "wB97X-D4", "basis": "def2-TZVP"}},
        "single_point_method": "DLPNO-CCSD(T)",
    }
    before = _counts(client)
    response = _preflight(client, "BatchOptimize", _BATCH_INPUT, method)
    assert response.status_code == 422
    assert _counts(client) == before


def test_successful_preflight_creates_no_job(client: TestClient) -> None:
    before = _counts(client)
    response = _preflight(client, "BatchOptimize", _BATCH_INPUT, _batch_method())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["would_create_task"] is False
    assert body["would_reserve"] is False
    assert _counts(client) == before


# ── route/basis conflicts (round-7 #3 classifier) ──────────────────────


def test_batch_basis_conflict_is_refused_by_preflight(client: TestClient) -> None:
    method = {
        "profile": "opt_freq_sp",
        "batch_roles": {
            "int": {"method": "wB97X-D4 def2-TZVP", "basis": "ma-def2-SVP"},
            "ts": {"method": "r2SCAN-3c"},
        },
    }
    response = _preflight(client, "BatchOptimize", _BATCH_INPUT, method)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"]["rule"] == "basis_conflict"
    assert detail["params"]["field"] == "batch_roles.int.basis"
    assert detail["params"]["conflicts"][0]["tokens"] == ["def2-TZVP", "ma-def2-SVP"]


def test_singlepoint_basis_conflict_is_refused_by_preflight(client: TestClient) -> None:
    method = {
        "levels": {
            "single_point": {
                "functional": "wB97X-D4 def2-TZVP",
                "basis": "def2-SVP",
            }
        }
    }
    response = _preflight(client, "singlepoint", {"input": "CCO"}, method)
    assert response.status_code == 422, response.text
    params = response.json()["detail"]["params"]
    assert params["rule"] == "basis_conflict"
    assert params["field"] == "levels.single_point.basis"


def test_singlepoint_aux_slot_refuses_an_orbital_basis() -> None:
    from acp.calculations.submission_validation import (
        SubmissionAnalysisError,
        analyze_singlepoint_submission,
    )

    with pytest.raises(SubmissionAnalysisError) as caught:
        analyze_singlepoint_submission(
            {},
            {
                "levels": {
                    "single_point": {
                        "functional": "wB97X-D4",
                        "aux_j_basis": "def2-TZVP",
                    }
                }
            },
        )
    assert caught.value.params["rule"] == "aux_basis_invalid"
    assert caught.value.params["field"] == "levels.single_point.aux_j_basis"


def test_submit_refuses_the_same_basis_conflict_as_preflight(client: TestClient) -> None:
    """Round-7 P1: submit must not accept a payload preflight already refuses."""
    method = {
        "profile": "opt_freq_sp",
        "batch_roles": {
            "int": {"method": "wB97X-D4 def2-TZVP", "basis": "ma-def2-SVP"},
            "ts": {"method": "r2SCAN-3c"},
        },
    }
    pre = _preflight(client, "BatchOptimize", _BATCH_INPUT, method)
    assert pre.status_code == 422, pre.text
    before = sum(dict(client.app.state.job_manager.store.counts()).values())
    response = client.post(
        "/api/v1/jobs",
        json={"workflow": "BatchOptimize", "input": _BATCH_INPUT, "method": method},
    )
    assert response.status_code == 422, response.text
    after = sum(dict(client.app.state.job_manager.store.counts()).values())
    assert after == before
    p = pre.json()["detail"]
    s = response.json()["detail"]
    assert p["code"] == s["code"] == "input_invalid"
    assert p["params"]["rule"] == s["params"]["rule"] == "basis_conflict"
    assert p["params"]["field"] == s["params"]["field"] == "batch_roles.int.basis"
