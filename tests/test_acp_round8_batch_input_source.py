"""Round-8 K2: ``input.items`` requires ``source_type=batch_structures``.

The external agent submitted a BatchOptimize payload whose ``input.items`` was
populated but whose ``source_type`` was missing.  Preflight said ``feasible:
true`` (the analyzer never asked whether the carrier could be materialized) and
the run failed ~30 s later with the generic "requires a batch artifact, items
file, or materialized structure" error.

These tests pin the lead decision:

* non-empty ``items`` + missing/wrong ``source_type`` is refused at **submit and
  preflight** with the same ``code``/``field``/``rule``
  (``input_invalid``/``input.source_type``/``missing_source_type``);
* the runtime never infers "items implies batch" — one field keeps one meaning;
* empty/missing ``items`` + ``from_artifact``/``items_file`` still work;
* the read-only analyzer projects the executed carrier under
  ``effective.input_source`` and adds ``input_source`` to ``validated_scope``;
* preflight stays zero-side-effect (no job rows, no task tree changes).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.api.submission_contract import SubmissionContractError, validate_workflow_submission
from acp.calculations.submission_validation import SubmissionAnalysisError, analysis_for

_XYZ = "2\nTAG: INT\nH 0 0 0\nH 0 0 0.7\n"
_METHOD: dict = {"profile": "opt_freq", "batch_roles": {"int": {"method": "r2SCAN-3c"}}}


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


def _items_input(**extra: object) -> dict:
    payload: dict = {"items": [{"xyz": _XYZ}], "charge": 0, "multiplicity": 1, **extra}
    return payload


def _preflight(client: TestClient, input_payload: dict, method: dict | None = None):
    return client.post(
        "/api/v1/preflight",
        json={"workflow": "BatchOptimize", "input": input_payload, "method": method or _METHOD},
    )


def _submit(client: TestClient, input_payload: dict, method: dict | None = None):
    return client.post(
        "/api/v1/jobs",
        json={"workflow": "BatchOptimize", "input": input_payload, "method": method or _METHOD},
    )


def _counts(client: TestClient) -> dict:
    return dict(client.app.state.job_manager.store.counts())


# ── the submit contract ─────────────────────────────────────────────────


def _contract_error(input_payload: dict) -> SubmissionContractError:
    with pytest.raises(SubmissionContractError) as caught:
        validate_workflow_submission(
            "BatchOptimize", input=input_payload, method=_METHOD, resources={}
        )
    return caught.value


def test_items_without_source_type_is_refused() -> None:
    error = _contract_error(_items_input())
    assert error.rule == "missing_source_type"
    assert error.field_name == "input.source_type"
    params = error.params()
    assert params["field"] == "input.source_type"
    assert params["items_count"] == 1
    assert params["source_type"] is None
    assert "batch_structures" in params["hint"]
    assert "from_artifact" in params["hint"]


def test_items_with_a_wrong_source_type_is_refused() -> None:
    error = _contract_error(_items_input(source_type="smiles"))
    assert error.rule == "missing_source_type"
    assert error.field_name == "input.source_type"
    assert error.params()["source_type"] == "smiles"


def test_items_with_batch_structures_passes() -> None:
    validate_workflow_submission(
        "BatchOptimize",
        input=_items_input(source_type="batch_structures"),
        method=_METHOD,
        resources={},
    )


def test_empty_items_with_from_artifact_still_passes() -> None:
    """The legacy create-now-fail-later shape must not be tightened."""
    validate_workflow_submission(
        "BatchOptimize",
        input={"items": [], "from_artifact": "RESULT/result_manifest.json"},
        method=_METHOD,
        resources={},
    )


def test_missing_items_with_items_file_still_passes() -> None:
    validate_workflow_submission(
        "BatchOptimize",
        input={"items_file": "inputs/items.json"},
        method=_METHOD,
        resources={},
    )


def test_single_inline_structure_still_passes_without_source_type() -> None:
    validate_workflow_submission(
        "BatchOptimize", input={"input": _XYZ, "charge": 0}, method=_METHOD, resources={}
    )


# ── the read-only analyzer projection ──────────────────────────────────


def test_effective_input_source_names_the_batch_carrier() -> None:
    analysis = analysis_for("BatchOptimize", _items_input(source_type="batch_structures"), _METHOD)
    assert analysis is not None
    assert "input_source" in analysis.validated_scope
    source = analysis.effective["input_source"]
    assert source["mode"] == "batch_structures"
    assert source["source_type"] == "batch_structures"
    assert source["items_count"] == 1
    assert source["carriers"] == ["items"]


def test_effective_input_source_names_the_artifact_carrier() -> None:
    analysis = analysis_for(
        "BatchOptimize",
        {"items": [], "from_artifact": "RESULT/result_manifest.json"},
        _METHOD,
    )
    assert analysis is not None
    source = analysis.effective["input_source"]
    assert source["mode"] == "from_artifact"
    assert source["items_count"] == 0
    assert source["carriers"] == ["from_artifact"]


def test_effective_input_source_names_the_items_file_carrier() -> None:
    analysis = analysis_for("BatchOptimize", {"items_file": "inputs/items.json"}, _METHOD)
    assert analysis is not None
    assert analysis.effective["input_source"]["mode"] == "items_file"


def test_analyzer_refuses_items_without_source_type_with_the_same_rule() -> None:
    with pytest.raises(SubmissionAnalysisError) as caught:
        analysis_for("BatchOptimize", _items_input(), _METHOD)
    params = caught.value.params
    assert params["rule"] == "missing_source_type"
    assert params["field"] == "input.source_type"
    assert params["items_count"] == 1


# ── HTTP: preflight and submit agree, zero side effects ────────────────


def test_preflight_and_submit_refuse_identically(client: TestClient) -> None:
    preflight = _preflight(client, _items_input())
    submit = _submit(client, _items_input())
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
    assert p["params"]["rule"] == "missing_source_type"
    assert p["params"]["field"] == "input.source_type"


def test_preflight_refusal_has_no_side_effects(client: TestClient, tmp_path: Path) -> None:
    before_counts = _counts(client)
    before_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    response = _preflight(client, _items_input(source_type="smiles"))
    assert response.status_code == 422
    assert _counts(client) == before_counts
    after_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert after_tree == before_tree


def test_valid_preflight_reports_the_batch_carrier(client: TestClient) -> None:
    response = _preflight(client, _items_input(source_type="batch_structures"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feasible"] is True
    assert body["would_create_task"] is False
    receipt = body["submission_analysis"]
    assert receipt is not None
    assert "input_source" in receipt["validated_scope"]
    assert receipt["effective"]["input_source"]["mode"] == "batch_structures"
    assert receipt["executed"] is None


def test_empty_items_artifact_preflight_passes(client: TestClient) -> None:
    response = _preflight(client, {"items": [], "from_artifact": "RESULT/result_manifest.json"})
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    assert receipt["effective"]["input_source"]["mode"] == "from_artifact"
