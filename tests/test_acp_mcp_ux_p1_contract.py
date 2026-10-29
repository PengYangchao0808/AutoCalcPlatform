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

import copy
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
from acp.scheduler.resources import resolve_job_resources, validate_resource_request


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
        yield test_client


def _table_counts(db_path: str) -> dict[str, int]:
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        names = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        ]
        return {
            name: connection.execute(f"select count(*) from {name}").fetchone()[0]
            for name in names
        }
    finally:
        connection.close()


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


def test_preflight_and_submit_report_the_same_violation(
    client: TestClient, tmp_path: Path
) -> None:
    manager = client.app.state.job_manager
    before_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    before_counts = dict(manager.store.counts())
    before_tables = _table_counts(str(manager.store.db_path))
    before_admission = client.app.state.admission.snapshot()
    body = _confsearch(method={"refinementPolicy": "rank1"})
    preflight = _error(client.post("/api/v1/preflight", json=body))
    submit = _error(client.post("/api/v1/jobs", json=body))
    assert preflight == submit
    # No side effects: no task, no reservation/budget movement, nothing queued.
    assert dict(manager.store.counts()) == before_counts
    assert _table_counts(str(manager.store.db_path)) == before_tables
    assert client.app.state.admission.snapshot() == before_admission
    assert not getattr(manager, "_submission_jobs", {})
    after_tree = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))
    assert after_tree == before_tree  # no task dir, prepare record or artifact


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


def test_workbench_shaped_confsearch_payload_is_accepted() -> None:
    """The real Workbench sends source_type/charge/multiplicity and a levels map."""
    payload = {
        "workflow": "Confsearch",
        "input": {
            "source_type": "smiles",
            "source": "CCO",
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "protocol": "xtb-crest",
            "profile": "default",
            "refinement_policy": "rank1",
            "confsearch_profile": "default",
            "levels": {"censo": {"ewin": 6.0}},
        },
        "resources": {"cores": 4},
    }
    validate_workflow_submission(**payload)


def test_batch_roles_with_flat_scalars_is_accepted() -> None:
    """Workbench sends the flat mirror next to batch_roles; it must not be refused."""
    validate_workflow_submission(
        "BatchOptimize",
        input={
            "source_type": "batch_structures",
            "items": [{"xyz": "H 0 0 0"}],
            "charge": 0,
            "multiplicity": 1,
        },
        method={
            "profile": "opt_freq",
            "batch_roles": {"int": {"role": "int", "method": "B3LYP"}},
            "optimization_method": "B3LYP",
            "temperature": 298.15,
        },
        resources={},
    )


def test_contract_does_not_mutate_inputs() -> None:
    payload = {
        "workflow": "Confsearch",
        "input": {"smiles": "CCO"},
        "method": {"protocol": "xtb-crest", "refinement_policy": "rank1"},
        "resources": {"cores": 4},
    }
    before = copy.deepcopy(payload)
    validate_workflow_submission(**payload)
    assert payload == before


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_resource_conflict_is_refused(client: TestClient, path: str) -> None:
    detail = _error(
        client.post(path, json=_confsearch(resources={"nproc": 4, "cores": 8}))
    )
    assert detail["params"] == {"field": "resources", "rule": "invalid_resource"}


@pytest.mark.parametrize("path", ["/api/v1/preflight", "/api/v1/jobs"])
def test_resource_bool_and_zero_alias(client: TestClient, path: str) -> None:
    bool_detail = _error(
        client.post(path, json=_confsearch(resources={"nproc": 4, "cores": True}))
    )
    assert bool_detail["params"]["rule"] == "invalid_resource"
    # "0" on a lower-priority alias is unset, not a conflict.
    response = client.post(path, json=_confsearch(resources={"nproc": 4, "cores": "0"}))
    assert response.status_code != 422 or response.json()["detail"]["params"]["field"] != "resources"


def test_workbench_confsearch_levels_are_accepted() -> None:
    validate_workflow_submission(
        "Confsearch",
        input={"source_type": "smiles", "source": "CCO", "charge": 0, "multiplicity": 1},
        method={
            "protocol": "censo-crest",
            "profile": "default",
            "refinement_policy": "rank1",
            "confsearch_profile": "default",
            "levels": {
                "confsearch": {
                    "engine": "censo",
                    "protocol": "censo-crest",
                    "confsearch_profile": "default",
                    "refinement_policy": "rank1",
                    "ewin": 6.0,
                    "refinement_threshold": 0.99,
                },
                "dft_opt": {"engine": "orca", "functional": "wB97X-D4", "basis": "def2-TZVP"},
                "refinement_sp": {"engine": "orca", "functional": "wB97X-D4"},
                "thermo": {"engine": "shermo", "temperature": 298.15, "pressure": 1.0},
            },
        },
        resources={"cores": 4},
    )


def test_nested_level_typo_is_refused() -> None:
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "Confsearch",
            input={"smiles": "CCO"},
            method={"protocol": "xtb-crest", "levels": {"dft_opt": {"functonal": "B3LYP"}}},
            resources={},
        )
    assert excinfo.value.params()["field"] == "method.levels.dft_opt.functonal"


def test_unknown_level_id_is_refused() -> None:
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "Confsearch",
            input={"smiles": "CCO"},
            method={"protocol": "xtb-crest", "levels": {"dftopt": {"functional": "B3LYP"}}},
            resources={},
        )
    assert excinfo.value.params()["rule"] == "unknown_field"


def test_batch_levels_accept_steps_and_reject_typos() -> None:
    validate_workflow_submission(
        "BatchOptimize",
        input={"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]},
        method={"profile": "opt_freq", "levels": {"batch": {"steps": ["optimize", "frequency"]}}},
        resources={},
    )
    with pytest.raises(SubmissionContractError):
        validate_workflow_submission(
            "BatchOptimize",
            input={"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]},
            method={"profile": "opt_freq", "levels": {"batch": {"optimze": True}}},
            resources={},
        )


def test_consumer_only_level_fields_are_accepted() -> None:
    """screening_sp / recalc_hess / aux_c_basis are real consumers, not UI fields."""
    validate_workflow_submission(
        "Confsearch",
        input={"smiles": "CCO"},
        method={
            "protocol": "censo-crest",
            "levels": {
                "refinement_threshold": 0.99,
                "dft_opt": {
                    "recalc_hess": "auto",
                    "aux_j_basis": "def2/J",
                    "aux_c_basis": "def2-TZVPPD",
                    "ri_approximation": "RIJCOSX",
                },
                "refinement_sp": {"aux_c_basis": "def2-TZVPPD"},
                "screening_sp": {"functional": "B3LYP"},
            },
        },
        resources={},
    )


def test_batch_mirror_must_match_batch_level() -> None:
    validate_workflow_submission(
        "BatchOptimize",
        input={"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]},
        method={
            "profile": "opt_freq",
            "batch_roles": {"int": {"role": "int", "method": "B3LYP"}},
            "optimization_method": "B3LYP",
            "temperature": 298.15,
            "levels": {"batch": {"temperature": 298.15}},
        },
        resources={},
    )
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "BatchOptimize",
            input={"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]},
            method={
                "profile": "opt_freq",
                "batch_roles": {"int": {"role": "int", "method": "B3LYP"}},
                "optimization_method": "B3LYP",
                "temperature": 298.15,
                "levels": {"batch": {"temperature": 310.15}},
            },
            resources={},
        )
    assert excinfo.value.params()["rule"] == "conflicting_fields"


@pytest.mark.parametrize(
    "bad",
    [{"nproc": 0}, {"nproc": -1}, {"nproc": "4.0"}, {"nproc": "1e999"}, {"nproc": True}],
)
def test_resource_invalid_values_refused_by_both_entries(client: TestClient, bad: dict[str, Any]) -> None:
    for path in ("/api/v1/preflight", "/api/v1/jobs"):
        detail = _error(client.post(path, json=_confsearch(resources=bad)))
        assert detail["params"]["field"] == "resources"


def test_local_and_remote_argv_parse_for_three_workflows(tmp_path: Path) -> None:
    """P1-03: the production local and remote builders must emit parseable argv."""
    from types import SimpleNamespace

    from acp.cli import build_parser
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.runner import JobRunner

    parser = build_parser()
    conf = JobSpec(
        workflow="Confsearch",
        name="conf",
        input={"smiles": "CCO"},
        method={
            "protocol": "censo-crest",
            "refinement_policy": "rank1",
            "levels": {"refinement_threshold": 0.99, "dft_opt": {"functional": "wB97X-D4"}},
        },
        resources={"nproc": 4, "mem": "8GB"},
    )
    batch = JobSpec(
        workflow="BatchOptimize",
        name="batch",
        input={"items_file": "/tmp/items.json"},
        method={"profile": "opt_freq", "optimization_method": "B3LYP"},
        resources={"nproc": 4, "mem": "8GB"},
    )
    nmr = JobSpec(
        workflow="nmr",
        name="nmr",
        input={
            "smiles": "CCO",
            "experiment": {"mode": "assigned", "content": "1 1.0 2.0"},
        },
        method={"max_conformers": 5},
        resources={"nproc": 4, "mem": "8GB"},
    )
    runner = SimpleNamespace(python="python")
    runner._build_nmr_cmd = lambda spec, work_dir: JobRunner._build_nmr_cmd(
        runner, spec, work_dir
    )
    runner._materialize_experiment = JobRunner._materialize_experiment
    for spec in (conf, batch, nmr):
        local = JobRunner._build_cmd(runner, spec, tmp_path, "input.xyz")
        assert local[:4] == ["python", "-m", "acp.cli", "run"]
        parser.parse_args(local[3:])
        remote = build_remote_cli_command(spec, python_executable="python")
        assert remote[:4] == ["python", "-m", "acp.cli", "run"]
        parser.parse_args(remote[3:])


def test_workbench_payloads_pass_the_http_entry(client: TestClient) -> None:
    """Real production shapes must not be refused by preflight."""
    conf = _confsearch(
        input={"source_type": "smiles", "source": "CCO", "charge": 0, "multiplicity": 1},
        method={
            "protocol": "censo-crest",
            "profile": "default",
            "refinement_policy": "rank1",
            "confsearch_profile": "default",
            "levels": {
                "confsearch": {"engine": "censo", "protocol": "censo-crest"},
                "dft_opt": {"engine": "orca", "functional": "wB97X-D4", "recalc_hess": "auto"},
                "refinement_sp": {"engine": "orca", "aux_c_basis": "def2-TZVPPD"},
                "thermo": {"engine": "shermo", "temperature": "298.15"},
            },
        },
    )
    response = client.post("/api/v1/preflight", json=conf)
    assert response.status_code == 200, response.text

    batch = {
        "workflow": "BatchOptimize",
        "name": "batch-contract",
        "input": {
            "source_type": "batch_structures",
            "items": [{"xyz": "O 0 0 0\nH 1 0 0\nH 0 1 0"}],
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "profile": "opt_freq",
            "batch_roles": {"int": {"role": "int"}},
            "levels": {"batch": {"steps": ["optimize", "frequency"]}},
        },
    }
    response = client.post("/api/v1/preflight", json=batch)
    assert response.status_code == 200, response.text


def test_resource_validation_does_not_mutate() -> None:
    resources = {"nproc": 4, "cores": "0", "mem": "8GB"}
    before = copy.deepcopy(resources)
    validate_resource_request(resources)
    assert resources == before


def test_batch_non_mirrored_flat_fields_are_refused() -> None:
    """Sol round 4: shadowed flat settings must not be swallowed."""
    base_input = {"source_type": "batch_structures", "items": [{"xyz": "H 0 0 0"}]}
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "BatchOptimize",
            input=base_input,
            method={
                "profile": "opt_freq",
                "batch_roles": {"int": {"method": "B3LYP"}},
                "optimization_method": "PBE0",
            },
            resources={},
        )
    assert excinfo.value.params()["rule"] == "conflicting_fields"
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "BatchOptimize",
            input=base_input,
            method={
                "profile": "opt_freq",
                "batch_roles": {"int": {"opt_recalc_hess": 5}},
                "minimum_opt_recalc_hess": 10,
            },
            resources={},
        )
    assert excinfo.value.params()["rule"] == "conflicting_fields"
    with pytest.raises(SubmissionContractError) as excinfo:
        validate_workflow_submission(
            "BatchOptimize",
            input=base_input,
            method={
                "profile": "opt_freq",
                "batch_roles": {"int": {"method": "B3LYP"}},
                "scf_orbital_inherit": True,
            },
            resources={},
        )
    assert excinfo.value.params()["rule"] == "shadowed_field"
