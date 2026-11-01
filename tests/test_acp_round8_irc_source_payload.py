"""Round-8 IRC: ``input.ts_source`` is server-derived, never a writable shell.

The published example used to contain ``ts_source: {"schema": "irc_ts_source_v1"}``.
That object is not the provenance the scheduler derives from
``source_job_id``/``source_product_id``: the manager compares the client object
with the derived values over ten locked fields, so the shell always failed at
submit with the misleading "IRC source evidence changed since the previous
submission" — while preflight (which never looked at the field) said feasible.

These tests pin the round-8 contract:

* a *partial* ``ts_source`` is refused at submit and preflight with the same
  ``code``/``field``/``rule`` (``input_invalid``/``input.ts_source``/
  ``irc_ts_source_partial``) and a hint to omit it;
* a *complete* object stays accepted (idempotent replay / CLI compatibility);
  equality with the derived provenance remains the manager's check (batch D);
* the analyzer projects ``effective.source_reference`` without reading any
  store, adds ``source_reference`` to ``validated_scope`` and emits a
  non-fatal advisory that real TS verification happens at submit time;
* the published contract example no longer advertises the shell.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from acp.api.submission_contract import (
    IRC_TS_SOURCE_LOCKED_FIELDS,
    SubmissionContractError,
    validate_workflow_submission,
)
from acp.calculations.submission_validation import analysis_for

_SOURCE = {"source_job_id": "20260101_001_BatchOptimize", "source_product_id": "ts_candidate"}


def _complete_ts_source() -> dict:
    return {
        "schema": "irc_ts_source_v1",
        "job_id": "20260101_001_BatchOptimize",
        "product_id": "ts_candidate",
        "geometry_sha256": "sha256:" + "a" * 64,
        "method": "r2SCAN-3c",
        "basis": "",
        "charge": 0,
        "multiplicity": 1,
        "imaginary_frequency_cm1": -420.5,
        "frequency_evidence_sha256": "sha256:" + "b" * 64,
        "source_completed_at": "2026-01-01T00:00:00+00:00",
    }


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


def _preflight(client: TestClient, input_payload: dict) -> object:
    return client.post(
        "/api/v1/preflight",
        json={"workflow": "irc", "input": input_payload, "method": {"maxpoints": 40}},
    )


def _submit(client: TestClient, input_payload: dict) -> object:
    return client.post(
        "/api/v1/jobs",
        json={"workflow": "irc", "input": input_payload, "method": {"maxpoints": 40}},
    )


def _contract_error(input_payload: dict) -> SubmissionContractError:
    with pytest.raises(SubmissionContractError) as caught:
        validate_workflow_submission(
            "irc", input=input_payload, method={"maxpoints": 40}, resources={}
        )
    return caught.value


# ── the submit contract ─────────────────────────────────────────────────


def test_partial_ts_source_is_refused_with_a_hint() -> None:
    error = _contract_error({**_SOURCE, "ts_source": {"schema": "irc_ts_source_v1"}})
    assert error.rule == "irc_ts_source_partial"
    assert error.field_name == "input.ts_source"
    params = error.params()
    assert params["field"] == "input.ts_source"
    assert set(params["missing"]) == set(IRC_TS_SOURCE_LOCKED_FIELDS)
    assert params["locked_fields"] == list(IRC_TS_SOURCE_LOCKED_FIELDS)
    assert "ts_source" in params["hint"]
    assert "source_job_id" in params["hint"]


def test_ts_source_missing_one_locked_field_is_refused() -> None:
    complete = _complete_ts_source()
    complete.pop("geometry_sha256")
    error = _contract_error({**_SOURCE, "ts_source": complete})
    assert error.rule == "irc_ts_source_partial"
    assert error.params()["missing"] == ["geometry_sha256"]


def test_non_object_ts_source_is_a_typed_type_error() -> None:
    error = _contract_error({**_SOURCE, "ts_source": "irc_ts_source_v1"})
    assert error.rule == "invalid_type"
    assert error.field_name == "input.ts_source"


def test_omitted_ts_source_passes() -> None:
    validate_workflow_submission("irc", input=_SOURCE, method={"maxpoints": 40}, resources={})


def test_complete_ts_source_is_still_accepted() -> None:
    validate_workflow_submission(
        "irc",
        input={**_SOURCE, "ts_source": _complete_ts_source()},
        method={"maxpoints": 40},
        resources={},
    )


def test_complete_ts_source_is_not_value_checked_at_the_contract() -> None:
    """Source-change detection stays the manager's job (batch D owns its text)."""
    changed = {**_complete_ts_source(), "geometry_sha256": "sha256:" + "c" * 64}
    validate_workflow_submission(
        "irc",
        input={**_SOURCE, "ts_source": changed},
        method={"maxpoints": 40},
        resources={},
    )


# ── HTTP: preflight and submit agree ───────────────────────────────────


def test_preflight_and_submit_refuse_a_partial_ts_source_identically(
    client: TestClient,
) -> None:
    payload = {**_SOURCE, "ts_source": {"schema": "irc_ts_source_v1"}}
    store = client.app.state.job_manager.store
    before = dict(store.counts())
    preflight = _preflight(client, payload)
    submit = _submit(client, payload)
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
    assert p["params"]["rule"] == "irc_ts_source_partial"
    assert p["params"]["field"] == "input.ts_source"
    # Neither entry reached a snapshot/dispatch side effect.
    assert dict(store.counts()) == before


def test_preflight_and_submit_refuse_a_non_object_ts_source_identically(
    client: TestClient,
) -> None:
    payload = {**_SOURCE, "ts_source": "irc_ts_source_v1"}
    preflight = _preflight(client, payload)
    submit = _submit(client, payload)
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
    assert p["params"]["rule"] == "invalid_type"
    assert p["params"]["field"] == "input.ts_source"


def test_preflight_with_omitted_ts_source_projects_the_resolution(
    client: TestClient,
) -> None:
    response = _preflight(client, _SOURCE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["would_create_task"] is False
    receipt = body["submission_analysis"]
    assert receipt is not None
    assert "source_reference" in receipt["validated_scope"]
    source = receipt["effective"]["source_reference"]
    assert source == {
        "source_job_id": "20260101_001_BatchOptimize",
        "source_product_id": "ts_candidate",
        "authored_ts_source": "absent",
        "resolution": "runtime_derived",
    }
    # Real TS verification is a submit-time fact, never a preflight claim.
    assert "irc_ts_source_runtime_verification" in receipt["unverified_checks"]
    assert receipt["executed"] is None


def test_preflight_with_a_complete_ts_source_marks_it_complete(
    client: TestClient,
) -> None:
    response = _preflight(client, {**_SOURCE, "ts_source": _complete_ts_source()})
    assert response.status_code == 200, response.text
    source = response.json()["submission_analysis"]["effective"]["source_reference"]
    assert source["authored_ts_source"] == "complete"
    assert source["resolution"] == "runtime_derived"


# ── the analyzer projection (pure) ─────────────────────────────────────


def test_analyzer_projects_absent_and_runtime_derived() -> None:
    analysis = analysis_for("irc", _SOURCE, {"maxpoints": 40})
    assert analysis is not None
    assert analysis.effective["source_reference"] == {
        "source_job_id": "20260101_001_BatchOptimize",
        "source_product_id": "ts_candidate",
        "authored_ts_source": "absent",
        "resolution": "runtime_derived",
    }
    assert "source_reference" in analysis.validated_scope


def test_analyzer_marks_a_partial_ts_source_without_raising() -> None:
    analysis = analysis_for(
        "irc",
        {**_SOURCE, "ts_source": {"schema": "irc_ts_source_v1"}},
        {"maxpoints": 40},
    )
    assert analysis is not None
    assert analysis.effective["source_reference"]["authored_ts_source"] == "partial"


def test_analyzer_marks_a_complete_ts_source() -> None:
    analysis = analysis_for(
        "irc", {**_SOURCE, "ts_source": _complete_ts_source()}, {"maxpoints": 40}
    )
    assert analysis is not None
    assert analysis.effective["source_reference"]["authored_ts_source"] == "complete"


def test_published_irc_example_no_longer_advertises_ts_source() -> None:
    from acp.api.workflow_contracts import describe_workflow

    block = describe_workflow("irc")
    assert "ts_source" not in block["examples"][0]["input"]
    # the example must pass the real validator
    example = block["examples"][0]
    validate_workflow_submission(
        example["workflow"],
        input=example["input"],
        method=example["method"],
        resources=example["resources"],
    )
