"""Round-5 PR-03: preflight / submit parity and the read-only analysis receipt.

The real agent hit ``feasible: true`` on preflight and ``missing: ["coordinate"]``
on submit.  These tests pin the plan's §7.5 acceptance list:

* static errors (missing/invalid coordinates, invalid protocol, IRC step,
  conflicting point aliases, tsmode source reference) produce the same
  ``code/field/rule`` in preflight and submit;
* a preflight refusal creates no job and changes no counts;
* the normal path returns the read-only analysis receipt
  (validated_scope / parameter_effects / effective / executed=None) so a
  client can distinguish requested / effective / executed;
* unavailable sources become ``unverified_checks`` instead of an unconditional
  feasibility claim.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_XYZ = "6\n\nC 0 0 0\nC 1.5 0 0\nC 2.5 1 0\nC 3.5 0 0\nC 4.5 1 0\nC 5.5 0 0\n"


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
    params_p = p.get("params") or {}
    params_s = s.get("params") or {}
    assert params_p.get("rule") == params_s.get("rule"), (params_p, params_s)
    assert params_p.get("field") == params_s.get("field"), (params_p, params_s)


# --------------------------------------------------------------------------- #
# parity: static errors agree between preflight and submit
# --------------------------------------------------------------------------- #


def test_preflight_and_submit_agree_on_static_errors(client: TestClient) -> None:
    # PES coordinate conflict (PR-01 shape)
    pes = {
        "source": {"source_type": "xyz_text", "xyz_text": _XYZ},
        "coordinate": {"kind": "distance", "atoms": [0, 5], "start": "4.90", "end": "1.55"},
        "coordinates": [{"kind": "distance", "atoms": [0, 4], "start": "4.90", "end": "1.55"}],
    }
    _assert_parity(
        _preflight(client, "PESsearch", pes), _submit(client, "PESsearch", pes)
    )


def test_irc_step_is_refused_identically(client: TestClient) -> None:
    body = {
        "source_job_id": "20990101_001_frequency",
        "source_product_id": "ts",
        "ts_source": {"schema": "irc_ts_source_v1"},
    }
    _assert_parity(
        _preflight(client, "irc", body, {"step": 0.1}),
        _submit(client, "irc", body, {"step": 0.1}),
    )


def test_scan_point_alias_conflict_is_refused_identically(client: TestClient) -> None:
    body = {"xyz_text": _XYZ}
    method = {
        "coordinate": "0,1,1.0,3.0",
        "scan_points": 10,
        "levels": {"scan": {"scan_coordinate_points": 20}},
    }
    _assert_parity(
        _preflight(client, "scan", body, method),
        _submit(client, "scan", body, method),
    )


def test_tsmode_missing_source_is_refused_identically(client: TestClient) -> None:
    body = {"source_mode_index": 6}
    _assert_parity(
        _preflight(client, "tsmode", body), _submit(client, "tsmode", body)
    )


def test_preflight_refusal_has_no_side_effects(client: TestClient) -> None:
    before = _counts(client)
    body = {"xyz_text": _XYZ}
    method = {
        "coordinate": "0,1,1.0,3.0",
        "scan_points": 10,
        "levels": {"scan": {"scan_coordinate_points": 20}},
    }
    response = _preflight(client, "scan", body, method)
    assert response.status_code == 422
    assert _counts(client) == before


# --------------------------------------------------------------------------- #
# analysis receipt
# --------------------------------------------------------------------------- #


def test_normal_pes_preflight_returns_the_read_only_receipt(client: TestClient) -> None:
    body = {
        "source": {"source_type": "xyz_text", "xyz_text": _XYZ},
        "coordinate": {"kind": "distance", "atoms": [0, 5], "start": "4.90", "end": "1.55"},
        "protocol": {"scan_driver": {"software": "orca"}, "scan_optimizer": {"method": "GFN2-xTB"}},
    }
    response = _preflight(client, "PESsearch", body)
    assert response.status_code == 200, response.text
    receipt = response.json()["submission_analysis"]
    assert receipt is not None
    assert receipt["contract_version"] == "acp2.submission.v1"
    assert "coordinates" in receipt["validated_scope"]
    assert "protocol" in receipt["validated_scope"]
    assert receipt["effective"]["execution_mode"] == "native_scan"
    assert receipt["executed"] is None
    dispositions = {item["field"]: item["disposition"] for item in receipt["parameter_effects"]}
    assert dispositions["input.coordinate/coordinates"] == "consumed"
    assert receipt["unverified_checks"] == []


def test_uncovered_workflow_has_no_receipt(client: TestClient) -> None:
    response = _preflight(client, "casscf", {"input": _XYZ})
    assert response.status_code == 200, response.text
    assert response.json()["submission_analysis"] is None


def test_irc_maxpoints_mirror_conflict_is_refused_in_analysis() -> None:
    from acp.calculations.submission_validation import analyze_irc_submission

    with pytest.raises(ValueError, match="maxpoints conflicts"):
        analyze_irc_submission(
            {"source_job_id": "j"},
            {"maxpoints": 40, "levels": {"irc": {"maxpoints": 60}}},
        )


def test_preflight_and_submit_agree_on_batch_manifest_cardinality(
    client: TestClient, tmp_path: Path
) -> None:
    """Round-6 #4: a two-structure manifest must fail preflight, not only submit."""
    from acp.storage.manifest import ResultManifest

    result_dir = tmp_path / "src_batch" / "RESULT"
    structures = result_dir / "structures"
    structures.mkdir(parents=True)
    for name in ("a", "b"):
        (structures / f"{name}.xyz").write_text(_XYZ, encoding="utf-8")
    manifest = ResultManifest(task_id="src", workflow="PESsearch", status="completed")
    manifest.publication = {"revision": 1, "status": "committed", "digest": "d"}
    for name in ("a", "b"):
        manifest.add_product(
            id=f"pes_candidate_{name}",
            label=name,
            path=f"structures/{name}.xyz",
            kind="structure",
            metadata={"selection_source": "manual", "candidate_id": name, "role": "INT"},
        )
    manifest.write(result_dir)
    payload = {"from_artifact": str(result_dir / "result_manifest.json")}
    pre = _preflight(client, "BatchOptimize", payload)
    sub = _submit(client, "BatchOptimize", payload)
    _assert_parity(pre, sub)
    assert pre.json()["detail"]["params"]["rule"] == "layout_limit"
