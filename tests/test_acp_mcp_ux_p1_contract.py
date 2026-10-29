"""P1: one submit/preflight contract, resource alias, and published limits.

Covers the four P1 acceptance clauses:

* unknown ``method`` keys, illegal enums and missing input fail on **both**
  entry points with the same ``input_invalid`` shape;
* the Confsearch ``max_conformers`` mapping is gone, so the field is refused
  instead of producing an unparsable argv;
* ``cores`` resolves to the same execution value as ``nproc``/``ncores`` and a
  conflict is refused;
* ``/api/capabilities`` publishes the admission instance's ceilings.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.api.submission_contract import (
    SubmissionContractError,
    validate_workflow_submission,
)
from acp.scheduler.jobs import JobSpec
from acp.scheduler.resources import resolve_job_resources


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def _confsearch(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workflow": "Confsearch",
        "name": "contract-test",
        "input": {"smiles": "CCO"},
        "method": {"protocol": "xtb-crest", "refinement_policy": "rank1"},
    }
    payload.update(overrides)
    return payload


def _error(response: Any) -> dict[str, Any]:
    assert response.status_code == 422, response.text
    return response.json()["detail"]


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_unknown_method_key_is_refused(client: TestClient, path: str) -> None:
    detail = _error(client.post(path, json=_confsearch(method={"refinementPolicy": "rank1"})))
    assert detail["code"] == "input_invalid"
    assert detail["params"]["field"] == "method.refinementPolicy"
    assert detail["params"]["rule"] == "unknown_field"
    assert "refinement_policy" in detail["params"]["allowed"]


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_illegal_enum_is_refused(client: TestClient, path: str) -> None:
    detail = _error(client.post(path, json=_confsearch(method={"refinement_policy": "screening"})))
    assert detail["params"] == {
        "field": "method.refinement_policy",
        "rule": "invalid_enum",
        "allowed": ["all", "cumulative-99", "rank1", "screen"],
    }


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_missing_input_is_refused(client: TestClient, path: str) -> None:
    detail = _error(client.post(path, json=_confsearch(input={})))
    assert detail["params"]["field"] == "input"
    assert detail["params"]["rule"] == "missing_input"


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_confsearch_max_conformers_is_refused(client: TestClient, path: str) -> None:
    """P1-03: the flag only exists on ``run nmr``; Confsearch must fail closed."""
    detail = _error(client.post(path, json=_confsearch(method={"max_conformers": 10})))
    assert detail["params"]["field"] == "method.max_conformers"
    assert detail["params"]["rule"] == "unknown_field"


def test_preflight_and_submit_report_the_same_violation(client: TestClient) -> None:
    before = sum(client.app.state.job_manager.store.counts().values())
    body = _confsearch(method={"refinementPolicy": "rank1"})
    preflight = _error(client.post("/api/v1/preflight", json=body))
    submit = _error(client.post("/api/v1/jobs", json=body))
    assert preflight == submit
    assert sum(client.app.state.job_manager.store.counts().values()) == before


def test_valid_confsearch_passes_the_contract() -> None:
    # The pure contract must not reject the documented shape.
    validate_workflow_submission(
        "Confsearch",
        input={"smiles": "CCO"},
        method={"protocol": "xtb-crest", "refinement_policy": "rank1"},
        resources={"nproc": 4},
    )


def test_valid_confsearch_preflight_still_answers(client: TestClient) -> None:
    response = client.post("/api/v1/preflight", json=_confsearch())
    assert response.status_code == 200, response.text
    assert "plan_digest" in response.json()


def test_cores_alias_resolves_and_conflicts_are_refused() -> None:
    spec = JobSpec(workflow="Confsearch", resources={"cores": 4, "mem": "1GB"})
    assert resolve_job_resources(spec)["nproc"] == 4
    with pytest.raises(ValueError, match="disagree"):
        resolve_job_resources(
            JobSpec(workflow="Confsearch", resources={"nproc": 4, "cores": 8, "mem": "1GB"})
        )


def test_capabilities_publishes_the_admission_limits(client: TestClient) -> None:
    limits = SimpleNamespace(max_cores=8, max_walltime_s=3600, default_walltime_s=600)
    snapshot = {"enforcement": "admission", "limits": {"max_cores": 8}}
    previous = client.app.state.admission
    client.app.state.admission = SimpleNamespace(limits=limits, snapshot=lambda: snapshot)
    try:
        payload = client.get("/api/capabilities").json()
    finally:
        client.app.state.admission = previous
    assert payload["limits"]["max_cores"] == 8
    assert payload["limits"]["max_walltime_s"] == 3600
    assert payload["limits"]["default_walltime_s"] == 600
    assert payload["limits"]["enforcement"] == "admission"


def _cli_help(command: str) -> str:
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-m", "acp", "run", command, "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout


def test_max_conformers_is_nmr_only_on_the_cli_surface() -> None:
    """P1-03: the flag exists on ``run nmr`` and nowhere on Confsearch."""
    assert "--max-conformers" in _cli_help("nmr")
    assert "--max-conformers" not in _cli_help("Confsearch")


def test_capabilities_match_admission_under_environment_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-05: discovery and the admission instance read the same ceilings."""
    monkeypatch.setenv("ACP_ADMISSION_MAX_CORES", "8")
    monkeypatch.setenv("ACP_ADMISSION_MAX_WALLTIME_S", "7200")
    monkeypatch.setenv("ACP_ADMISSION_DEFAULT_WALLTIME_S", "1800")
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as scoped:
        snapshot = scoped.app.state.admission.snapshot()
        payload = scoped.get("/api/capabilities").json()
    assert payload["limits"]["max_cores"] == snapshot["limits"]["max_cores"] == 8
    assert payload["limits"]["max_walltime_s"] == snapshot["limits"]["max_walltime_s"] == 7200
    assert payload["limits"]["default_walltime_s"] == 1800
    assert payload["limits"]["enforcement"] == "admission"
