"""Round-8 #5: the resolved Hessian policy per Batch role.

``levels`` content is type-checked only, so "``opt_recalc_hess``: 5" versus
``"auto"`` was invisible at the API layer even though the difference is
statically decidable: ``"auto"`` follows the element-graded default
(``cccp.qc.hessian_policy``), an integer is an explicit interval.

These tests pin ``effective.roles.<role>.hessian_resolution``:

* TS default → requested ``5``, resolved ``5``, ``explicit_interval``;
* INT default → requested ``"auto"``, resolved by elements (light → 0,
  non-light → 10), ``auto``;
* an explicit interval/off wins and is reported as such;
* no inline geometry → ``resolved_interval: null`` + ``symbols_required`` and
  never an exception (artifact/items-file carriers stay feasible);
* the projection is pure (no store/file reads) and the preflight route exposes
  it unchanged.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.calculations.submission_validation import analysis_for

_H2 = "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n"
_H2S = "2\n\nS 0 0 0\nH 0 0 1.3\n"


def _input(xyz: str) -> dict:
    return {"source_type": "batch_structures", "items": [{"xyz": xyz}]}


def _roles(method: dict, input_payload: dict | None = None) -> dict:
    analysis = analysis_for(
        "BatchOptimize",
        input_payload if input_payload is not None else _input(_H2),
        method,
    )
    assert analysis is not None
    return analysis.effective["roles"]


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


# ── explicit / default recalc policies ─────────────────────────────────


def test_ts_default_recalc_is_an_explicit_interval() -> None:
    ts = _roles({"batch_roles": {"ts": {"method": "r2SCAN-3c"}}})["ts"]
    hessian = ts["hessian_resolution"]
    assert hessian["initial"] == {"requested": "calculate", "source": "role_resolved"}
    recalc = hessian["recalc"]
    assert recalc["requested"] == 5
    assert recalc["resolved_interval"] == 5
    assert recalc["reason"] == "explicit_interval"
    assert recalc["source"] == "explicit"


def test_int_default_auto_resolves_on_light_elements() -> None:
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c"}}})["int"]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["requested"] == "auto"
    assert recalc["resolved_interval"] == 0
    assert recalc["reason"] == "auto"
    assert recalc["source"] == "config"
    assert recalc["heavy_elements"] == []


def test_int_default_auto_resolves_on_non_light_elements() -> None:
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c"}}}, _input(_H2S))["int"]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["requested"] == "auto"
    assert recalc["resolved_interval"] == 10
    assert recalc["reason"] == "auto"
    assert recalc["heavy_elements"] == ["S"]


def test_explicit_interval_is_reported_as_such() -> None:
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c", "opt_recalc_hess": 7}}})[
        "int"
    ]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["requested"] == 7
    assert recalc["resolved_interval"] == 7
    assert recalc["reason"] == "explicit_interval"
    assert recalc["source"] == "explicit"


def test_explicit_zero_disables_the_exact_hessian() -> None:
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c", "opt_recalc_hess": 0}}})[
        "int"
    ]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["requested"] == 0
    assert recalc["resolved_interval"] == 0
    assert recalc["reason"] == "explicit_off"


def test_legacy_flat_payload_is_resolved_too() -> None:
    roles = _roles({"optimization_method": "r2SCAN-3c"})
    assert roles["int"]["hessian_resolution"]["recalc"]["reason"] == "auto"
    # TS keeps the documented per-role default even in the flat shape.
    assert roles["ts"]["hessian_resolution"]["recalc"]["requested"] == 5


def test_multiple_items_union_their_elements() -> None:
    payload = {
        "source_type": "batch_structures",
        "items": [{"xyz": _H2}, {"xyz": _H2S}],
    }
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c"}}}, payload)["int"]
    assert int_role["hessian_resolution"]["recalc"]["resolved_interval"] == 10


# ── no inline geometry: symbols_required, never an exception ───────────


def test_no_items_reports_symbols_required_without_raising() -> None:
    analysis = analysis_for(
        "BatchOptimize",
        {"from_artifact": "/nonexistent/result_manifest.json"},
        {},
    )
    assert analysis is not None
    int_role = analysis.effective["roles"]["int"]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["requested"] == "auto"
    assert recalc["resolved_interval"] is None
    assert recalc["reason"] == "symbols_required"
    assert recalc["source"] is None
    # An explicit interval needs no symbols and still resolves.
    ts = analysis.effective["roles"]["ts"]["hessian_resolution"]["recalc"]
    assert ts["requested"] == 5 and ts["resolved_interval"] == 5


def test_unparseable_inline_item_is_skipped_not_fatal() -> None:
    payload = {"source_type": "batch_structures", "items": [{"xyz": "not an xyz"}]}
    int_role = _roles({"batch_roles": {"int": {"method": "r2SCAN-3c"}}}, payload)["int"]
    recalc = int_role["hessian_resolution"]["recalc"]
    assert recalc["resolved_interval"] is None
    assert recalc["reason"] == "symbols_required"


def test_analyzer_does_not_read_the_artifact_carrier(tmp_path: Path) -> None:
    """Purity: a nonexistent artifact path is not opened or stat-ed."""
    marker = tmp_path / "nope"
    payload = {"from_artifact": str(marker / "result_manifest.json")}
    before = sorted(path.name for path in tmp_path.rglob("*"))
    analysis = analysis_for("BatchOptimize", payload, {})
    assert analysis is not None
    assert not marker.exists()
    assert sorted(path.name for path in tmp_path.rglob("*")) == before


# ── the route exposes the same projection ──────────────────────────────


def test_preflight_exposes_hessian_resolution(client: TestClient) -> None:
    response = client.post(
        "/api/v1/preflight",
        json={
            "workflow": "BatchOptimize",
            "input": _input(_H2S),
            "method": {"batch_roles": {"int": {"method": "r2SCAN-3c"}}},
        },
    )
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    hessian = receipt["effective"]["roles"]["int"]["hessian_resolution"]
    assert hessian["recalc"]["requested"] == "auto"
    assert hessian["recalc"]["resolved_interval"] == 10
    assert hessian["recalc"]["reason"] == "auto"


def test_published_batch_contract_points_at_the_effective_hessian_block() -> None:
    from acp.api.workflow_contracts import describe_workflow

    note = describe_workflow("BatchOptimize")["method"]["levels_note"]
    assert "hessian_resolution" in note
    assert "recalc_hess" in note
