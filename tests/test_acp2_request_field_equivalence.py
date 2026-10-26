# ruff: noqa: F811 - the imported pytest fixture is requested by name
"""M0-06 (G23) — one field policy, one effective JobSpec, one request digest.

The gaps this file pins down (``reports/acp2_delivery_gap_audit_20261026``):

* the v1 submit DTO silently dropped every field it did not declare, so a typo
  such as ``execution_mod`` degraded into a *local* run instead of an error;
* the MCP adapter rebuilt a trimmed payload (``_ControllerSubmit`` /
  ``_ControllerArtifacts.prepare``), so a legal target/naming field could reach
  HTTP but not MCP;
* an unknown remote node produced a 400 with a top-level scheduler code at HTTP
  and an opaque ``backend_failure`` at MCP, never one application error;
* the request digest was whatever each entry happened to serialise.

The tests below run the **real** FastAPI Controller (uvicorn, real JobStore) and
the real ``acp.services_mcp_bootstrap`` composition, plus the real ``acp task
execute`` subprocess.  Scientific dispatch stays suppressed (``fake`` workflow):
this is field/identity evidence, not QC evidence.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.entrypoints.application_params import (
    ENTRY_CLI,
    ENTRY_HTTP,
    ENTRY_MCP,
    LEGACY_REQUEST_FIELD_ALLOWLIST,
    PREFLIGHT_FIELD_POLICY,
    REQUEST_PREFLIGHT,
    REQUEST_PREPARE,
    REQUEST_SUBMIT,
    SUBMIT_FIELD_POLICY,
    ApplicationParamError,
    canonical_submit_request,
    field_policy_gaps,
    validate_request_fields,
)
from acp_services.runtime.idempotency import (
    LEGACY_REQUEST_DIGEST_VERSION,
    REQUEST_DIGEST_VERSION,
    canonical_request_digest,
    canonical_request_digest_for_version,
    verify_request_digest,
)
from tests.test_acp2_runtime_mcp_backends import (  # noqa: F401 - pytest fixture
    _runtime,
    _tool,
    live_controller,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
CONTRACT = REPO_ROOT / "docs/reference/next-change/acp2-contracts/runtime/application-api.v1.json"

#: The one logical submit request used by the cross-entry tests.  ``source_type``
#: mirrors ``acp task execute --input C`` so the two entries send the same input
#: payload; ``node_tags`` carries the dirty entries the CLI produces as well.
LEGAL_REQUEST: dict[str, Any] = {
    "workflow": "fake",
    "name": "m0-06-equivalence",
    "input": {"source": "C", "source_type": "smiles"},
    "method": {},
    "resources": {},
    "tags": ["m0-06"],
    "node_tags": ["gpu", " gpu ", ""],
    "target_node": "local",
    "molecule_name": "ethanol",
    "task_name": "sp",
    "remark": "equiv",
}


# ---------------------------------------------------------------------------
# 1. Anti-drift: DTO ↔ policy table ↔ registered contract
# ---------------------------------------------------------------------------


def test_submit_field_policy_is_exhaustive_over_the_v1_dtos() -> None:
    """A new DTO field without a policy row fails this test (M0-06 exit)."""
    from acp.api.v1_schemas import V1JobCreateRequest, V1PreflightRequest, V1PrepareRequest

    for kind, model in (
        (REQUEST_SUBMIT, V1JobCreateRequest),
        (REQUEST_PREFLIGHT, V1PreflightRequest),
        (REQUEST_PREPARE, V1PrepareRequest),
    ):
        unregistered, stale = field_policy_gaps(model.model_fields, request_kind=kind)
        assert unregistered == (), (kind, "unregistered", unregistered)
        assert stale == (), (kind, "stale", stale)
        assert model.model_config.get("extra") == "forbid", kind


def test_registered_contract_matches_the_field_policy() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    submit_fields = set(contract["operations"]["submit"]["request"]["fields"])
    preflight_fields = set(contract["operations"]["preflight"]["request"]["fields"])
    # The contract declares the *accepted* fields; a server-path field is declared
    # by the DTO only so its rejection can be explicit (unsupported_server_path).
    accepted = {
        name
        for name, row in SUBMIT_FIELD_POLICY.items()
        if row.disposition != "server_path_rejected"
    }
    accepted_preflight = {
        name
        for name, row in PREFLIGHT_FIELD_POLICY.items()
        if row.disposition != "server_path_rejected"
    }
    assert submit_fields == accepted
    assert preflight_fields == accepted_preflight
    assert accepted_preflight == accepted
    assert contract["operations"]["submit"]["request"]["additional_properties"] is False
    documented = contract["submit_field_policy"]
    assert documented["legacy_allowlist"] == LEGACY_REQUEST_FIELD_ALLOWLIST
    for name, row in SUBMIT_FIELD_POLICY.items():
        assert documented["fields"][name]["disposition"] == row.disposition, name
    assert documented["request_digest"]["version"] == REQUEST_DIGEST_VERSION
    for name in ("output_dir", "config_path"):
        assert SUBMIT_FIELD_POLICY[name].disposition == "server_path_rejected", (
            f"{name} is a server path a client may not choose"
        )


def test_frontend_and_bridge_request_keys_are_registered_consumers() -> None:
    """Mechanical consumer scan: every key a shipped client sends is registered.

    The scan is deliberately narrow and stable (the frozen submit-body builder,
    the node-selection helper and the bridge's validate/submit argument block),
    so a new client field cannot be added without registering it in the policy.
    """
    wizard = (REPO_ROOT / "frontend/js/workbench/features/wizard/submit-edit.js").read_text(
        encoding="utf-8"
    )
    body_block = wizard.split("function buildStructureJobBody(spec) {", 1)[1].split("\n}", 1)[0]
    wizard_keys = set(re.findall(r"^\s{4}([a-z_]+):", body_block, re.MULTILINE))
    assert {"workflow", "input", "method", "resources"} <= wizard_keys

    node = (REPO_ROOT / "frontend/js/workbench/features/reaction-builder.js").read_text(
        encoding="utf-8"
    )
    node_keys = set(re.findall(r"body\.([a-z_]+)\s*=", node))
    assert {"target_node", "node_tags"} <= node_keys

    bridge = (REPO_ROOT / "rph/packages/bridge/src/index.ts").read_text(encoding="utf-8")
    submit_block = bridge.split('case "validate":', 1)[1].split('case "status":', 1)[0]
    bridge_keys = set(re.findall(r'put\("([a-z_]+)"', submit_block))
    # ``explicit`` belongs to the analyze/evidence command that shares this block;
    # the submit fields are exactly the frozen six.
    submit_bridge_keys = bridge_keys - {"explicit"}
    assert submit_bridge_keys == {
        "workflow",
        "input",
        "method",
        "project_id",
        "resources",
        "idempotency_key",
    }

    declared = set(SUBMIT_FIELD_POLICY)
    assert wizard_keys | node_keys <= declared, (wizard_keys | node_keys) - declared
    assert submit_bridge_keys <= declared, submit_bridge_keys - declared


def test_legacy_allowlist_is_empty_and_every_row_would_be_scheduled() -> None:
    """No 'ignore an unknown field and warn' mode: the allowlist is explicit or empty."""
    assert LEGACY_REQUEST_FIELD_ALLOWLIST == {}
    for name, row in LEGACY_REQUEST_FIELD_ALLOWLIST.items():
        assert row.removal_version and row.exit_condition, name


# ---------------------------------------------------------------------------
# 2. Unknown / server-path fields are the same rejection
# ---------------------------------------------------------------------------


def _field_error(payload: dict[str, Any], *, entry: str) -> ApplicationParamError:
    with pytest.raises(ApplicationParamError) as info:
        validate_request_fields(payload, request_kind=REQUEST_SUBMIT, entry=entry)
    return info.value


def test_execution_mod_typo_is_input_invalid_at_every_entry() -> None:
    payload = {"workflow": "fake", "input": {}, "execution_mod": "remote"}
    seen = {}
    for entry in (ENTRY_HTTP, ENTRY_MCP, ENTRY_CLI):
        error = _field_error(payload, entry=entry)
        assert error.code == "input_invalid"
        assert error.params["rule"] == "unknown_field"
        assert error.params["field"] == "execution_mod"
        seen[entry] = (error.code, error.params["field"], error.params["rule"])
    assert len(set(seen.values())) == 1


def test_client_chosen_server_paths_are_explicitly_rejected() -> None:
    for field in ("output_dir", "config_path"):
        for entry in (ENTRY_HTTP, ENTRY_MCP):
            error = _field_error({"workflow": "fake", "input": {}, field: "/tmp/x"}, entry=entry)
            assert error.params["rule"] == "unsupported_server_path"
            assert error.params["field"] == field


# ---------------------------------------------------------------------------
# 3. HTTP ↔ MCP ↔ CLI: same code + params for an unknown remote node
# ---------------------------------------------------------------------------


def _http_field_error(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid", detail
    return detail


def _cli_execute(run_root: Path, *argv: str) -> tuple[int, dict[str, Any]]:
    run_root.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "ACP_RUN_ROOT": str(run_root),
    }
    # Deterministic project: the deployment pin must not leak from the dev shell.
    env.pop("ACP_SERVER_PROJECT", None)
    completed = subprocess.run(
        [PYTHON, "-m", "acp.cli", "task", "execute", "--run-root", str(run_root), *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=180,
    )
    lines = [line for line in completed.stdout.strip().splitlines() if line.startswith("{")]
    payload = json.loads(lines[-1]) if lines else {}
    return completed.returncode, payload


def test_unknown_remote_node_is_one_application_error_everywhere(
    tmp_path: Path, live_controller: Any
) -> None:
    from acp.api.server import create_app

    run_root = tmp_path / "http"
    run_root.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(run_root=run_root, max_running=1)) as client:
        detail = _http_field_error(
            client, {"workflow": "fake", "input": {"source": "C"}, "target_node": "ghost"}
        )
    assert detail["params"]["rule"] == "unknown_target_node"
    assert detail["params"]["field"] == "target_node"

    mcp_runtime = _runtime(live_controller)
    result = _tool(
        mcp_runtime,
        "acp_submit",
        {"workflow": "fake", "input": {"source": "C"}, "target_node": "ghost"},
    )
    assert result["isError"] is True
    mcp_error = result["structuredContent"]["error"]
    assert mcp_error["code"] == "input_invalid"
    assert mcp_error["params"]["rule"] == "unknown_target_node"
    assert mcp_error["params"]["field"] == "target_node"

    code, cli_payload = _cli_execute(
        tmp_path / "cli", "--workflow", "fake", "--input", "C", "--target-node", "ghost"
    )
    assert code == 2, cli_payload
    assert cli_payload["error"] == "input_invalid"
    assert cli_payload["params"]["rule"] == "unknown_target_node"
    assert cli_payload["params"]["field"] == "target_node"

    # One application code, one field/rule pair.  The MCP adapter adds its own
    # ``command`` key to params (long-standing adapter context), so compare the
    # decision fields rather than dict equality.
    assert detail["params"]["rule"] == mcp_error["params"]["rule"] == cli_payload["params"]["rule"]
    assert (
        detail["params"]["field"] == mcp_error["params"]["field"] == cli_payload["params"]["field"]
    )


def test_preflight_validates_the_same_target_before_and_after_submit(
    tmp_path: Path, live_controller: Any
) -> None:
    """Preflight and submit share one execution-target check (M0-06).

    A pinned unknown node is reported as an infeasible preflight with the *same*
    ``input_invalid`` code and ``rule`` the submit rejects with — over HTTP and
    over the MCP adapter (which forwards the request instead of rebuilding it).
    """
    from acp.api.server import create_app

    body = {"workflow": "fake", "input": {"source": "C"}, "target_node": "ghost"}
    run_root = tmp_path / "http"
    run_root.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(run_root=run_root, max_running=1)) as client:
        http_preflight = client.post("/api/v1/preflight", json=body)
        assert http_preflight.status_code == 200, http_preflight.text
        projection = http_preflight.json()
        assert projection["feasible"] is False
        reason = projection["reasons"][0]
        assert reason["code"] == "input_invalid"
        assert reason["rule"] == "unknown_target_node"
        assert reason["field"] == "target_node"
        assert projection["would_create_task"] is False
        assert projection["would_reserve"] is False

    mcp_runtime = _runtime(live_controller)
    result = _tool(mcp_runtime, "acp_preflight", body)
    assert result["isError"] is not True, result
    data = result["structuredContent"]["data"]
    assert data["executed"] is False
    mcp_projection = data["estimate"]
    assert mcp_projection["feasible"] is False
    mcp_reason = mcp_projection["reasons"][0]
    assert mcp_reason["code"] == "input_invalid"
    assert mcp_reason["rule"] == "unknown_target_node"
    assert mcp_reason["field"] == "target_node"


def test_execution_mod_typo_over_http_and_mcp_is_the_same_envelope(
    tmp_path: Path, live_controller: Any
) -> None:
    from acp.api.server import create_app

    run_root = tmp_path / "http"
    run_root.mkdir(parents=True, exist_ok=True)
    with TestClient(create_app(run_root=run_root, max_running=1)) as client:
        detail = _http_field_error(
            client,
            {"workflow": "fake", "input": {"source": "C"}, "execution_mod": "remote"},
        )
    assert detail["params"]["rule"] == "unknown_field"
    assert detail["params"]["field"] == "execution_mod"

    mcp_runtime = _runtime(live_controller)
    result = _tool(
        mcp_runtime,
        "acp_submit",
        {"workflow": "fake", "input": {"source": "C"}, "execution_mod": "remote"},
    )
    assert result["isError"] is True
    mcp_error = result["structuredContent"]["error"]
    assert mcp_error["code"] == "input_invalid"
    assert mcp_error["params"]["rule"] == "unknown_field"
    assert mcp_error["params"]["field"] == "execution_mod"


# ---------------------------------------------------------------------------
# 4. Legal fields: same effective JobSpec and same full request digest
# ---------------------------------------------------------------------------

_SPEC_FIELDS = (
    "workflow",
    "name",
    "input",
    "method",
    "resources",
    "tags",
    "node_tags",
    "project_id",
    "execution_mode",
    "target_node",
    "molecule_name",
    "task_name",
    "remark",
)


def _spec_view(spec: Any) -> dict[str, Any]:
    return {name: getattr(spec, name) for name in _SPEC_FIELDS}


def _claim_row(db_path: Path, key: str) -> tuple[str, str]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT request_digest, request_digest_version FROM idempotency_records "
            "WHERE idempotency_key=?",
            (key,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, f"no idempotency row for {key}"
    return str(row[0]), str(row[1])


def test_legal_target_and_naming_fields_form_one_spec_and_one_digest(
    tmp_path: Path, live_controller: Any
) -> None:
    from acp_services.store.store import JobStore

    key = "m0-06-equivalence-key"
    body = dict(LEGAL_REQUEST, idempotency_key=key)
    request = urllib.request.Request(
        live_controller.base_url + "/api/v1/jobs",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Idempotency-Key": key},
    )
    with urllib.request.urlopen(request, timeout=30) as answer:
        assert answer.status == 201, answer.read()
        created = json.loads(answer.read())
    http_record = live_controller.manager.get(str(created["job_id"]))
    assert http_record is not None
    http_digest, http_version = _claim_row(live_controller.manager.store.db_path, key)
    assert http_version == REQUEST_DIGEST_VERSION

    code, cli_payload = _cli_execute(
        tmp_path / "cli",
        "--workflow",
        str(LEGAL_REQUEST["workflow"]),
        "--input",
        "C",
        "--name",
        str(LEGAL_REQUEST["name"]),
        "--tag",
        "m0-06",
        "--node-tag",
        "gpu",
        "--node-tag",
        "gpu",
        "--target-node",
        str(LEGAL_REQUEST["target_node"]),
        "--molecule-name",
        str(LEGAL_REQUEST["molecule_name"]),
        "--task-name",
        str(LEGAL_REQUEST["task_name"]),
        "--remark",
        str(LEGAL_REQUEST["remark"]),
        "--idempotency-key",
        key,
    )
    assert code == 0, cli_payload
    cli_digest, cli_version = _claim_row(tmp_path / "cli" / "acp_jobs.db", key)
    assert cli_version == REQUEST_DIGEST_VERSION

    cli_store = JobStore(tmp_path / "cli" / "acp_jobs.db")
    cli_record = cli_store.get(str(cli_payload["job_id"]))
    assert cli_record is not None

    # The CLI retry with the same key and the same effective request replays the
    # same task (one task, one attempt) instead of creating a second one.
    replay_code, replay_payload = _cli_execute(
        tmp_path / "cli",
        "--workflow",
        str(LEGAL_REQUEST["workflow"]),
        "--input",
        "C",
        "--name",
        str(LEGAL_REQUEST["name"]),
        "--tag",
        "m0-06",
        "--node-tag",
        "gpu",
        "--node-tag",
        "gpu",
        "--target-node",
        str(LEGAL_REQUEST["target_node"]),
        "--molecule-name",
        str(LEGAL_REQUEST["molecule_name"]),
        "--task-name",
        str(LEGAL_REQUEST["task_name"]),
        "--remark",
        str(LEGAL_REQUEST["remark"]),
        "--idempotency-key",
        key,
    )
    assert replay_code == 0, replay_payload
    assert replay_payload["job_id"] == cli_payload["job_id"]
    assert replay_payload["idempotent_replay"] is True
    assert len(cli_store.list(limit=100)) == 1

    assert _spec_view(http_record.spec) == _spec_view(cli_record.spec)
    assert http_digest == cli_digest
    # And the digest is the canonical request of the full legal field set, not a
    # partially rebuilt subset (the MCP adapter forwards the same body).
    expected = canonical_request_digest(canonical_submit_request(body, entry=ENTRY_HTTP))
    assert http_digest == expected


def test_same_key_with_a_changed_legal_target_is_a_conflict_not_a_replay(
    tmp_path: Path,
) -> None:
    from acp.api.server import create_app

    run_root = tmp_path / "http"
    run_root.mkdir(parents=True, exist_ok=True)
    key = "m0-06-conflict-key"
    with TestClient(create_app(run_root=run_root, max_running=1)) as client:
        first = client.post(
            "/api/v1/jobs",
            json={
                "workflow": "fake",
                "input": {"source": "C"},
                "target_node": "local",
                "idempotency_key": key,
            },
        )
        assert first.status_code == 201, first.text
        tasks_before = len(client.app.state.job_manager.store.list(limit=1000))
        second = client.post(
            "/api/v1/jobs",
            json={"workflow": "fake", "input": {"source": "C"}, "idempotency_key": key},
        )
        assert second.status_code == 409, second.text
        detail = second.json()["detail"]
        assert detail["code"] == "idempotency_conflict"
        assert detail["retryable"] is False
        tasks_after = len(client.app.state.job_manager.store.list(limit=1000))
        assert tasks_after == tasks_before


# ---------------------------------------------------------------------------
# 5. Prepare: the MCP adapter no longer rebuilds the request
# ---------------------------------------------------------------------------


def test_prepare_forwards_the_callers_request_unchanged() -> None:
    from acp.services_mcp_bootstrap import _ControllerArtifacts

    captured: dict[str, Any] = {}

    class _Client:
        def prepare(self, request: dict[str, Any]) -> dict[str, Any]:
            captured.update(request)
            return {"operation_id": "op_m0_06", "state": "queued"}

    request = {
        "schema_version": 1,
        "purpose": "artifact",
        "job_id": "job-1",
        "artifact_id": "catalog",
        "attempt": 2,
        "generation": 3,
        "relative_path": "RESULT/catalog.json",
        "expected_size_bytes": 10,
        "media_type": "application/json",
    }
    payload = _ControllerArtifacts(_Client()).prepare(
        request, idempotency_key="m0-06-prepare", workspace_id="ws"
    )
    assert captured == {**request, "idempotency_key": "m0-06-prepare"}
    assert payload["operation_ref"] == "op_m0_06"

    with pytest.raises(Exception) as unknown:
        _ControllerArtifacts(_Client()).prepare(
            {**request, "not_a_prepare_field": 1}, idempotency_key="", workspace_id="ws"
        )
    error = unknown.value
    assert getattr(error, "code", "") == "input_invalid"
    assert getattr(error, "params", {}).get("rule") == "unknown_field"


# ---------------------------------------------------------------------------
# 6. Digest versioning: old rows verify or reconcile, never "tampered"
# ---------------------------------------------------------------------------


def test_digest_versions_share_one_canonical_request_for_a_legacy_payload() -> None:
    payload = canonical_submit_request(
        {"workflow": "fake", "input": {"source": "C"}, "project_id": "p"}, entry=ENTRY_HTTP
    )
    current = canonical_request_digest(payload)
    legacy = canonical_request_digest_for_version(LEGACY_REQUEST_DIGEST_VERSION, payload)
    assert current == legacy
    assert canonical_request_digest_for_version("99", payload) is None


def test_legacy_digest_row_replays_or_reconciles_by_its_frozen_table(tmp_path: Path) -> None:
    from acp_services.store.store import JobStore

    store = JobStore(tmp_path / "jobs.db")
    payload = canonical_submit_request(
        {"workflow": "fake", "input": {"source": "C"}, "target_node": "local"},
        entry=ENTRY_HTTP,
    )
    legacy_digest = canonical_request_digest_for_version(LEGACY_REQUEST_DIGEST_VERSION, payload)
    assert legacy_digest is not None
    assert verify_request_digest(legacy_digest, LEGACY_REQUEST_DIGEST_VERSION, payload)
    assert not verify_request_digest(legacy_digest, "99", payload)
    scope = "local\x1falpha"

    # A pre-M0-06 row (rule "1") whose digest bytes equal the current request is
    # the SAME request, not a version conflict: the expired lease is taken over.
    store.claim_idempotency(
        scope,
        "submit",
        "m0-06-legacy-same",
        legacy_digest,
        now="2026-01-01T00:00:00+00:00",
        request_digest_version=LEGACY_REQUEST_DIGEST_VERSION,
        principal_id="local",
        project_id="alpha",
    )
    same = store.claim_idempotency(
        scope,
        "submit",
        "m0-06-legacy-same",
        canonical_request_digest(payload),
        request_payload=payload,
    )
    assert same.created, same
    assert same.detail == "takeover"
    assert same.claim is not None
    assert same.claim.request_digest_version == LEGACY_REQUEST_DIGEST_VERSION, (
        "a verified legacy row keeps its original rule version (historical fact)"
    )

    # A row of another rule whose digest cannot be re-derived from this request is
    # a reconcile conflict: not replayed, not "tampered", and no second task.
    store.claim_idempotency(
        scope,
        "submit",
        "m0-06-legacy-other",
        "sha256:" + "0" * 64,
        now="2026-01-01T00:00:00+00:00",
        request_digest_version=LEGACY_REQUEST_DIGEST_VERSION,
        principal_id="local",
        project_id="alpha",
    )
    conflict = store.claim_idempotency(
        scope,
        "submit",
        "m0-06-legacy-other",
        canonical_request_digest(payload),
        request_payload=payload,
    )
    assert conflict.conflict
    assert "reconcile" in conflict.detail
    assert conflict.task_ref is None
