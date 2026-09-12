"""Tests for the BatchOptimize config-preview endpoint."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=1))


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path, monkeypatch) as c:
        yield c


# ── helpers ──────────────────────────────────────────────────────────────

_URL = "/api/v1/batch-optimize/config-preview"


# ── default payload ──────────────────────────────────────────────────────


class TestDefaultPayload:
    """Empty body → all defaults, both roles populated."""

    def test_schema_version(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert data["schema"] == "batch_optimize_preview_v1"

    def test_common_section_present(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        data = resp.json()
        assert "common" in data
        assert isinstance(data["common"], dict)

    def test_roles_have_both_keys(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        roles = resp.json()["roles"]
        assert "int" in roles
        assert "ts" in roles

    def test_roles_have_effective_and_sources(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            role = resp.json()["roles"][role_key]
            assert "effective" in role
            assert "sources" in role

    def test_ts_default_trust_radius(self, client: TestClient) -> None:
        """TS role defaults: trust_radius=0.3, initial_hessian=calculate, recalc_hess=5."""
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        eff = ts["effective"]
        assert eff["opt_trust_radius"] == 0.3
        assert eff["opt_initial_hessian"] == "calculate"
        assert eff["opt_recalc_hess"] == 5

    def test_ts_default_sources_are_role_default(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        sources = ts["sources"]
        assert sources["opt_trust_radius"] == "role_default"
        assert sources["opt_initial_hessian"] == "role_default"
        assert sources["opt_recalc_hess"] == "role_default"

    def test_int_omits_trust_hessian_recalc(self, client: TestClient) -> None:
        """INT role has no role defaults → trust/hessian/recalc absent."""
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        int_eff = resp.json()["roles"]["int"]["effective"]
        assert "opt_trust_radius" not in int_eff
        assert "opt_initial_hessian" not in int_eff
        assert "opt_recalc_hess" not in int_eff

    def test_engine_constants_shared(self, client: TestClient) -> None:
        """max_cycles=200, opt_level=Tight (catalog default), scf trio defaults."""
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["max_cycles"] == 200
            # Catalog default for opt_convergence is "Tight" (capitalised)
            assert eff["opt_level"] == "Tight"
            assert eff["scf_maxiter"] == 300
            # Catalog default for scf_convergence is "Tight"
            assert eff["scf_convergence"] == "Tight"
            assert eff["scf_strategy"] == "normal"

    def test_shared_sources_are_default(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            sources = resp.json()["roles"][role_key]["sources"]
            assert sources["max_cycles"] == "default"
            assert sources["opt_level"] == "default"
            assert sources["scf_maxiter"] == "default"
            assert sources["scf_convergence"] == "default"
            assert sources["scf_strategy"] == "default"


# ── user overrides ───────────────────────────────────────────────────────


class TestUserOverrides:
    """User-provided values → 'user' source."""

    def test_user_opt_convergence(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_convergence": "VeryTight"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["opt_level"] == "VeryTight"
            src = resp.json()["roles"][role_key]["sources"]
            assert src["opt_level"] == "user"

    def test_user_opt_max_iter(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_max_iter": 500}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["max_cycles"] == 500
            src = resp.json()["roles"][role_key]["sources"]
            assert src["max_cycles"] == "user"

    def test_user_scf_strategy(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_strategy": "slowconv"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["scf_strategy"] == "slowconv"
            src = resp.json()["roles"][role_key]["sources"]
            assert src["scf_strategy"] == "user"

    def test_user_scf_max_iter(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_max_iter": 500}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["scf_maxiter"] == 500
            src = resp.json()["roles"][role_key]["sources"]
            assert src["scf_maxiter"] == "user"

    def test_user_scf_convergence(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_convergence": "VeryTight"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["scf_convergence"] == "VeryTight"
            src = resp.json()["roles"][role_key]["sources"]
            assert src["scf_convergence"] == "user"

    def test_user_common_opt_trust(self, client: TestClient) -> None:
        """Common opt_trust_radius → 'user' source for both roles."""
        resp = client.post(_URL, json={"method": {"opt_trust_radius": 0.25}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            eff = resp.json()["roles"][role_key]["effective"]
            assert eff["opt_trust_radius"] == 0.25
            src = resp.json()["roles"][role_key]["sources"]
            assert src["opt_trust_radius"] == "user"


# ── role overrides ───────────────────────────────────────────────────────


class TestRoleOverrides:
    """TS-specific overrides only appear for TS role."""

    def test_ts_role_trust(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"transition_state_opt_trust_radius": 0.15}})
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        assert ts["effective"]["opt_trust_radius"] == 0.15
        assert ts["sources"]["opt_trust_radius"] == "user"
        # INT should not have trust_radius from a TS override
        int_eff = resp.json()["roles"]["int"]["effective"]
        assert "opt_trust_radius" not in int_eff

    def test_ts_role_initial_hessian(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"transition_state_opt_initial_hessian": "model"}})
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        assert ts["effective"]["opt_initial_hessian"] == "model"
        assert ts["sources"]["opt_initial_hessian"] == "user"

    def test_ts_role_recalc_hess(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"transition_state_opt_recalc_hess": 10}})
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        assert ts["effective"]["opt_recalc_hess"] == 10
        assert ts["sources"]["opt_recalc_hess"] == "user"

    def test_int_role_trust(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"minimum_opt_trust_radius": 0.5}})
        assert resp.status_code == 200
        int_role = resp.json()["roles"]["int"]
        assert int_role["effective"]["opt_trust_radius"] == 0.5
        assert int_role["sources"]["opt_trust_radius"] == "user"

    def test_role_override_over_common(self, client: TestClient) -> None:
        """Role override takes precedence over common field."""
        resp = client.post(
            _URL,
            json={
                "method": {
                    "opt_trust_radius": 0.2,
                    "transition_state_opt_trust_radius": 0.4,
                }
            },
        )
        assert resp.status_code == 200
        ts = resp.json()["roles"]["ts"]
        assert ts["effective"]["opt_trust_radius"] == 0.4
        assert ts["sources"]["opt_trust_radius"] == "user"
        # INT gets the common value
        int_role = resp.json()["roles"]["int"]
        assert int_role["effective"]["opt_trust_radius"] == 0.2
        assert int_role["sources"]["opt_trust_radius"] == "user"


# ── invalid input → 422 ─────────────────────────────────────────────────


class TestValidation:
    """Invalid enum / hessian values → 422.  Min/max for int/float is NOT in
    normalize_and_validate_method_config (validated downstream by engine)."""

    def test_invalid_opt_recalc_hess_string(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_recalc_hess": "bogus"}})
        assert resp.status_code == 422

    def test_invalid_opt_recalc_hess_bool(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_recalc_hess": True}})
        assert resp.status_code == 422

    def test_invalid_opt_convergence_bad_enum(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_convergence": "SuperTight"}})
        assert resp.status_code == 422

    def test_invalid_scf_strategy_bad_enum(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_strategy": "turbo"}})
        assert resp.status_code == 422

    def test_invalid_scf_convergence_bad_enum(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_convergence": "extreme"}})
        assert resp.status_code == 422

    def test_invalid_ts_recalc_hess_string(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"transition_state_opt_recalc_hess": "wrong"}})
        assert resp.status_code == 422

    def test_int_scf_max_iter_zero_passes_through(self, client: TestClient) -> None:
        """Min/max not validated by normalize — passes through to engine."""
        resp = client.post(_URL, json={"method": {"scf_max_iter": 0}})
        assert resp.status_code == 200

    def test_int_scf_max_iter_negative_passes_through(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_max_iter": -5}})
        assert resp.status_code == 200

    def test_float_opt_trust_out_of_range_passes(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_trust_radius": 5.0}})
        assert resp.status_code == 200


# ── orca_summary ─────────────────────────────────────────────────────────


class TestOrcaSummary:
    """ORCA keyword strings mirror engine/ORCA mapping."""

    def test_default_ts_summary(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        ts_summary = resp.json()["orca_summary"]["ts"]
        assert "TightOpt" in ts_summary
        assert "TightSCF" in ts_summary
        assert "MaxIter 200" in ts_summary
        assert "Trust 0.3" in ts_summary
        assert "Calc_Hess" in ts_summary
        assert "Recalc_Hess 5" in ts_summary

    def test_default_int_summary_no_trust(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {}})
        assert resp.status_code == 200
        int_summary = resp.json()["orca_summary"]["int"]
        assert "Trust 0.3" not in " ".join(int_summary)
        assert "Calc_Hess" not in int_summary
        assert "Recalc_Hess" not in " ".join(int_summary)
        # But still has the shared keywords
        assert "TightOpt" in int_summary
        assert "TightSCF" in int_summary
        assert "MaxIter 200" in int_summary

    def test_slowconv_strategy(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_strategy": "slowconv"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            summary = resp.json()["orca_summary"][role_key]
            assert "SlowConv" in summary

    def test_soscf_strategy(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_strategy": "soscf"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            summary = resp.json()["orca_summary"][role_key]
            assert "SOSCF" in summary

    def test_normal_strategy_no_strategy_keyword(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"scf_strategy": "normal"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            summary = resp.json()["orca_summary"][role_key]
            assert "SlowConv" not in summary
            assert "SOSCF" not in summary

    def test_verytight_opt(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_convergence": "VeryTight"}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            summary = resp.json()["orca_summary"][role_key]
            assert "VeryTightOpt" in summary

    def test_custom_max_iter(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"opt_max_iter": 300}})
        assert resp.status_code == 200
        for role_key in ("int", "ts"):
            summary = resp.json()["orca_summary"][role_key]
            assert "MaxIter 300" in summary

    def test_no_calc_hess_when_model(self, client: TestClient) -> None:
        resp = client.post(_URL, json={"method": {"transition_state_opt_initial_hessian": "model"}})
        assert resp.status_code == 200
        ts_summary = resp.json()["orca_summary"]["ts"]
        assert "Calc_Hess" not in ts_summary
