"""B1-0 contract suite: the real app, the mounted routers, one closed loop.

Unlike the per-module suites, this file constructs the production application
with :func:`acp.api.server.create_app` and never calls ``include_router`` — the
seven science/read templates are exercised through whatever ``server.py``
mounts.  The research store is the lifespan-created one on
``<run_root>/acp_research.db``; prepared artifacts flow through the real
``PrepareOperationService`` and ``ArtifactStore``.

Scope (deliberately compact): templates/capabilities, preflight side-effect
freedom and its three refusals, one preflight → submit → status → view closed
loop plus the stale-fingerprint rejection, the legacy manifest view (validated
against the frozen schema), the remote mixed-cache matrix at route level,
published evidence claims, bounded artifact reads, research reads through the
lifespan store, and one zero-side-effect sweep.

Every JSON document is validated in-file by ``assert_matches`` — a small JSON
Schema 2020-12 interpreter for exactly the keywords the frozen contracts use.
An unknown keyword is a hard ``AssertionError`` naming it, never a silent
ignore.
"""

# pyright: reportAny=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnusedCallResult=false, reportFunctionMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportImplicitStringConcatenation=false, reportIndexIssue=false, reportOperatorIssue=false
from __future__ import annotations

import json
import re
import sqlite3
import time
from collections.abc import Generator, Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FRONTEND_CONTRACT = _REPO_ROOT / "frontend/js/workbench/contracts/experience_contract.js"
_SCHEMA_DIR = _REPO_ROOT / "docs/reference/next-change/acp2-contracts/experience"
_FIXTURE_DIR = _SCHEMA_DIR / "fixtures"

_CAPABILITIES = "/api/v2/science/capabilities"
_PREFLIGHT = "/api/v2/science/preflight"
_STATUS = "/api/v1/science/tasks/{job_id}/attempts/{attempt}/status"
_VIEW = "/api/v2/science/tasks/{job_id}/attempts/{attempt}/view"
_ARTIFACT = "/api/v2/science/tasks/{job_id}/attempts/{attempt}/artifacts/{artifact_id}/read"
_PROPOSAL = "/api/v2/research/proposals/{proposal_id}"
_RECORD = "/api/v2/research/records/{research_id}"

_PRINCIPAL = "principal_local"
_PROPOSAL_ID = "prop-b1-0-suite"
_RECORD_ID = "res-b1-0-suite"
_RELEASE_HEX = "a" * 64
_UPDATED_AT = "2026-10-25T00:00:00+00:00"
_XYZ = "3\nH2O\nO 0 0 0\nH 0 0 0.96\nH 0 0.93 -0.24\n"

_SIX_DEFAULTS = frozenset(
    {"method", "charge", "multiplicity", "solvent", "temperature", "standardState"}
)
_PROPOSAL_FORBIDDEN_KEYS = frozenset({"status", "state", "lifecycle", "job_id", "attempt"})


# ---------------------------------------------------------------------------
# In-file JSON Schema validator (contract keywords only)
# ---------------------------------------------------------------------------

_ANNOTATIONS = frozenset(
    {
        "$schema",
        "$id",
        "$comment",
        "title",
        "description",
        "default",
        "examples",
        "deprecated",
        "readOnly",
        "writeOnly",
        "schema_version",
    }
)
_SUPPORTED = frozenset(
    {
        "$ref",
        "$defs",
        "type",
        "required",
        "properties",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "pattern",
        "minimum",
        "maxLength",
        "minLength",
        "minItems",
        "uniqueItems",
        "format",
        "anyOf",
        "allOf",
        "oneOf",
        "not",
    }
)


def _resolve_ref(root: Mapping[str, Any], ref: str) -> Any:
    prefix = "#/$defs/"
    assert ref.startswith(prefix), f"unsupported $ref target: {ref!r}"
    name = ref[len(prefix) :]
    document = root.get("$defs")
    assert isinstance(document, Mapping) and name in document, f"unresolvable $ref: {ref!r}"
    return document[name]


def _json_type_ok(instance: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, Mapping)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    raise AssertionError(f"unsupported JSON type: {expected!r}")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _format_ok(value: str, fmt: str) -> bool:
    if fmt != "date-time":
        raise AssertionError(f"unsupported format: {fmt!r}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _collect(
    instance: Any,
    schema: Any,
    root: Mapping[str, Any],
    path: str,
    errors: list[str],
) -> None:
    assert isinstance(schema, Mapping), f"{path}: schema is not an object"
    for keyword in schema:
        if keyword in _ANNOTATIONS or keyword == "$defs":
            continue
        if keyword not in _SUPPORTED:
            raise AssertionError(f"{path}: unsupported schema keyword {keyword!r}")
        if keyword == "$ref":
            _collect(instance, _resolve_ref(root, schema["$ref"]), root, path, errors)

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_json_type_ok(instance, item) for item in types):
            errors.append(f"{path}: expected type {expected_type!r}, got {type(instance).__name__}")
            return

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, str):
        if "pattern" in schema and re.search(str(schema["pattern"]), instance) is None:
            errors.append(f"{path}: {instance!r} does not match {schema['pattern']!r}")
        if "minLength" in schema and len(instance) < int(schema["minLength"]):
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > int(schema["maxLength"]):
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "format" in schema and not _format_ok(instance, str(schema["format"])):
            errors.append(f"{path}: {instance!r} is not a valid {schema['format']}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance!r} is below minimum {schema['minimum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"]):
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if schema.get("uniqueItems") is True:
            seen = [_canonical(item) for item in instance]
            if len(seen) != len(set(seen)):
                errors.append(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                _collect(item, schema["items"], root, f"{path}[{index}]", errors)

    if isinstance(instance, Mapping):
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if name not in instance:
                    errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties")
        declared = properties if isinstance(properties, Mapping) else {}
        for name, subschema in declared.items():
            if name in instance:
                _collect(instance[name], subschema, root, f"{path}.{name}", errors)
        if "additionalProperties" in schema:
            extra = [name for name in instance if name not in declared]
            additional = schema["additionalProperties"]
            if additional is False and extra:
                errors.append(f"{path}: unexpected properties {sorted(extra)!r}")
            elif isinstance(additional, Mapping):
                for name in extra:
                    _collect(instance[name], additional, root, f"{path}.{name}", errors)

    if "anyOf" in schema:
        variants = schema["anyOf"]
        matched = 0
        for variant in variants:
            inner: list[str] = []
            _collect(instance, variant, root, path, inner)
            if not inner:
                matched += 1
        if matched == 0:
            errors.append(f"{path}: no anyOf variant matched")
    if "oneOf" in schema:
        matched = 0
        for variant in schema["oneOf"]:
            inner = []
            _collect(instance, variant, root, path, inner)
            if not inner:
                matched += 1
        if matched != 1:
            errors.append(f"{path}: {matched} oneOf variants matched, expected exactly 1")
    if "allOf" in schema:
        for variant in schema["allOf"]:
            _collect(instance, variant, root, path, errors)
    if "not" in schema:
        inner = []
        _collect(instance, schema["not"], root, path, inner)
        if not inner:
            errors.append(f"{path}: matched a forbidden 'not' schema")


def assert_matches(instance: Any, schema: Mapping[str, Any], *, root: Any = None) -> None:
    """Assert *instance* conforms to *schema* (raising on any unknown keyword)."""

    document = root if root is not None else schema
    errors: list[str] = []
    _collect(instance, schema, document, "$", errors)
    assert not errors, "schema validation failed:\n" + "\n".join(errors)


def _load_schema(name: str) -> dict[str, Any]:
    schema = json.loads((_SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    for root_key in ("schema", "schema_dialect", "ui_dialect"):
        schema.pop(root_key, None)
    return schema


def _load_fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _acp_tools_from_frontend() -> list[str]:
    source = _FRONTEND_CONTRACT.read_text(encoding="utf-8")
    match = re.search(r"var ACP_TOOLS = \[(.*?)\];", source, re.DOTALL)
    assert match is not None, "ACP_TOOLS block not found in the frontend contract"
    return re.findall(r'"([^"]+)"', match.group(1))


# ---------------------------------------------------------------------------
# Module fixture: the real app, once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Generator[TestClient, None, None]:
    run_root = tmp_path_factory.mktemp("b1-0-api-science")
    environment = pytest.MonkeyPatch()
    environment.setenv("ACP_PUBLIC_ORIGIN", "")
    environment.setenv("ACP_RUN_ROOT", str(run_root))
    environment.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    from acp.api.server import create_app

    app = create_app(run_root=run_root, max_running=1)
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        environment.undo()


def _manager(client: TestClient) -> Any:
    return client.app.state.job_manager


def _project(client: TestClient) -> str:
    return str(_manager(client).default_project_id)


def _side_effects(client: TestClient) -> tuple[dict[str, int], dict[str, int]]:
    manager = _manager(client)
    counts = manager.store.counts()
    tables: dict[str, int] = {}
    with sqlite3.connect(str(manager.store.db_path)) as connection:
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in ("data_operations", "data_transfer_publications"):
            if table in existing:
                tables[table] = int(
                    connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
            else:
                tables[table] = 0
    return counts, tables


def _seed_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    attempt: int = 1,
    revision: int = 1,
    manifest: bool = True,
) -> Any:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.storage.manifest import ResultManifest

    manager = _manager(client)
    work_dir.mkdir(parents=True, exist_ok=True)
    if manifest:
        document = ResultManifest(task_id=job_id, workflow="optimize", status="completed")
        document.add_product(
            id="step_0_optimize",
            label="Optimized structure",
            path="opt.xyz",
            kind="structure",
        )
        document.write(work_dir / "RESULT")
    spec = JobSpec(
        workflow="optimize",
        name=job_id,
        molecule_name="CCO",
        task_name="opt",
        remark="r",
        project_id=_project(client),
        input={"source": "CCO"},
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        project_id=_project(client),
        attempt=attempt,
        revision=revision,
        updated_at=_UPDATED_AT,
        created_at=_UPDATED_AT,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)
    return record


def _write_manifest(root: Path, *, product_id: str, sidecar_attempt: int | None = None) -> None:
    from acp.storage.manifest import ResultManifest

    manifest = ResultManifest(task_id="remote-job", workflow="optimize", status="completed")
    manifest.add_product(
        id=product_id,
        label=product_id,
        path=f"{product_id}.xyz",
        kind="structure",
    )
    path = manifest.write(root / "RESULT")
    if sidecar_attempt is not None:
        path.with_name(path.name + ".attempt").write_text(
            json.dumps({"attempt": sidecar_attempt}), encoding="utf-8"
        )


def _write_receipt(root: Path, attempt: int, *, digest: str = _RELEASE_HEX) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempt": attempt,
        "state": "completed",
        "revision": 3,
        "written_at": _UPDATED_AT,
        "release_digest": digest,
    }
    (root / "execution_receipt.json").write_text(json.dumps(payload), encoding="utf-8")


def _enqueue_artifact(
    client: TestClient,
    *,
    job_id: str,
    resource_id: str,
    generation: int = 1,
    payload: dict[str, Any] | None = None,
) -> Any:
    from acp_services.data.contracts import ResourceKind
    from acp_services.data.operations import PrepareRequest

    service = _manager(client)._prepare_operations
    return service.enqueue(
        PrepareRequest(
            job_id=job_id,
            attempt=1,
            resource_id=resource_id,
            resource_kind=ResourceKind.ARTIFACT,
            resource_digest=f"content-{resource_id}",
            permission_scope="local-single",
            resource_generation=generation,
            payload=payload or {"purpose": "artifact"},
        )
    )


def _lease_operation(service: Any, operation_id: str) -> Any:
    for _ in range(100):
        lease = service.lease_next()
        assert lease is not None, f"operation {operation_id!r} never became leasable"
        if lease.operation_id == operation_id:
            return lease
    raise AssertionError(f"could not lease operation {operation_id!r}")


def _publish_artifact(client: TestClient, operation: Any, body: bytes) -> Any:
    from acp_services.data.artifacts import ArtifactRef, ArtifactStore
    from acp_services.data.contracts import Availability
    from acp_services.data.operations import PrepareCompletion

    service = _manager(client)._prepare_operations
    store = ArtifactStore(_manager(client).run_root / "artifact-store")
    published = store.publish(
        ArtifactRef(
            job_id=operation.job_id,
            attempt=operation.attempt,
            resource_id=operation.resource_id,
            generation=operation.resource_generation,
            availability=Availability.PREPARING,
            relative_path=f"{operation.resource_id}.body",
        ),
        body,
    )
    lease = _lease_operation(service, operation.operation_id)
    service.complete(
        lease,
        PrepareCompletion(
            availability=Availability.READY,
            result={"purpose": "artifact"},
            size_bytes=published.size_bytes,
            sha256=published.digest.removeprefix("sha256:"),
        ),
    )
    return published


def _ensure_research_documents(client: TestClient) -> dict[str, dict[str, Any]]:
    from acp_services.data.research_resources import (
        PROPOSAL_SCHEMA,
        RESEARCH_RECORD_SCHEMA,
        ResearchResourceNotFoundError,
        ResearchResourceRequest,
        research_resource_key,
    )

    project = _project(client)
    store = client.app.state.research_resources
    proposal = _load_fixture("proposal.valid.json")
    proposal["project_id"] = project
    proposal["proposal_id"] = _PROPOSAL_ID
    record = _load_fixture("research-record.valid.json")
    record["project_id"] = project
    record["research_id"] = _RECORD_ID
    for task_ref in record["task_refs"]:
        task_ref["project_id"] = project

    documents = {
        "proposal": (PROPOSAL_SCHEMA, proposal),
        "record": (RESEARCH_RECORD_SCHEMA, record),
    }
    for kind, (schema, document) in documents.items():
        document_id = str(document["proposal_id" if kind == "proposal" else "research_id"])
        key = research_resource_key(project, kind, document_id)
        try:
            store.read(key, principal_id=_PRINCIPAL, project_id=project)
        except ResearchResourceNotFoundError:
            store.create(
                ResearchResourceRequest(
                    resource_id=key,
                    schema=schema,
                    document=document,
                    principal_id=_PRINCIPAL,
                    project_id=project,
                )
            )
    return {kind: document for kind, (_schema, document) in documents.items()}


def _cancel_and_wait(client: TestClient, job_id: str) -> None:
    manager = _manager(client)
    if manager.get(job_id) is None:
        return
    try:
        client.post(f"/api/v1/jobs/{job_id}/cancel")
    except Exception:  # noqa: BLE001 - cleanup must never mask the assertion
        pass
    for _ in range(60):
        record = manager.get(job_id)
        if record is None or record.status.value in {"completed", "failed", "cancelled"}:
            return
        if job_id not in manager._submission_jobs:
            return
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# 1-2. Templates and capabilities
# ---------------------------------------------------------------------------


def test_openapi_exposes_all_seven_templates(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]

    assert _CAPABILITIES in paths
    assert _PREFLIGHT in paths
    assert _STATUS in paths
    assert _VIEW in paths
    assert _PROPOSAL.format(proposal_id="{proposal_id}") in paths
    assert _RECORD.format(research_id="{research_id}") in paths
    artifact_paths = [path for path in paths if re.search(r"/artifacts/\{[^}]+\}/read$", path)]
    assert len(artifact_paths) == 1, artifact_paths
    assert artifact_paths[0].startswith("/api/v2/science/tasks/{job_id}/attempts/{attempt}/")


def test_capabilities_are_metadata_only_and_match_frontend(client: TestClient) -> None:
    response = client.get(_CAPABILITIES, params={"project_id": _project(client)})

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"protocol_version", "tools", "capabilities"}
    tools = body["tools"]
    assert set(tools) == set(_acp_tools_from_frontend())
    assert tools == sorted(tools)

    questions = body["capabilities"]["questions"]
    assert len(questions) == 3
    for question in questions:
        defaults = question["defaults"]
        assert set(defaults) == _SIX_DEFAULTS
        for value in defaults.values():
            assert set(value) == {"value", "source"}

    by_workflow = {question["workflow"]: question for question in questions}
    frequency = by_workflow["frequency"]["defaults"]
    assert frequency["method"]["value"] is None
    assert frequency["method"]["source"] == "no published default"
    assert frequency["temperature"]["value"] == 298.15
    assert frequency["standardState"]["value"] is None
    assert frequency["standardState"]["source"] == "not_applicable"
    assert "fixture" not in json.dumps(body)


# ---------------------------------------------------------------------------
# 3-5. Preflight and the closed loop
# ---------------------------------------------------------------------------


def _singlepoint_payload() -> dict[str, Any]:
    return {
        "workflow": "singlepoint",
        "input": {
            "source_type": "xyz_text",
            "source": _XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "method": {
            "schema_id": "dft_singlepoint",
            "profile_id": "default",
            "levels": {
                "single_point": {
                    "engine": "orca",
                    "functional": "r2SCAN-3c",
                    "basis": "def2-TZVP",
                }
            },
        },
        "resources": {"nproc": 2, "mem": "2GB"},
        "molecule_name": "water",
        "remark": "",
    }


def _preflight(client: TestClient, payload: Mapping[str, Any]) -> Any:
    return client.post(_PREFLIGHT, params={"project_id": _project(client)}, json=dict(payload))


def test_preflight_is_zero_side_effect_and_fingerprint_is_plan_digest(client: TestClient) -> None:
    payload = _singlepoint_payload()
    before = _side_effects(client)

    response = _preflight(client, payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["executed"] is False
    assert body["metering_consumed"] is False
    assert body["transfers_attempted"] is False
    assert isinstance(body["plan"]["plan_digest"], str) and body["plan"]["plan_digest"]
    assert body["preflight_fingerprint"] == body["plan"]["plan_digest"]
    assert body["plan"]["workflow"] == "singlepoint"
    assert body["plan"]["project_id"] == _project(client)
    assert isinstance(body["estimate"]["feasible"], bool)
    assert body["estimate"]["nproc"] == 2
    assert body["estimate"]["mem"] == "2GB"
    assert _side_effects(client) == before

    second = _preflight(client, payload)
    assert second.status_code == 200, second.text
    assert second.json()["preflight_fingerprint"] == body["preflight_fingerprint"]
    assert _side_effects(client) == before


def test_preflight_rejects_empty_body(client: TestClient) -> None:
    before = _side_effects(client)
    response = client.post(_PREFLIGHT, params={"project_id": _project(client)}, json={})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "input_invalid"
    assert detail["params"] == {"field": "workflow"}
    assert _side_effects(client) == before


def test_preflight_rejects_unknown_and_pinned_mismatch(client: TestClient) -> None:
    payload = _singlepoint_payload()
    before = _side_effects(client)

    unknown = client.post(_PREFLIGHT, params={"project_id": "ghost-project"}, json=payload)
    assert unknown.status_code == 403, unknown.text
    assert unknown.json()["detail"]["code"] == "access_denied"
    assert unknown.json()["detail"]["params"]["rule"] == "project_scope_mismatch"

    from acp_services.workspace.server_identity import ServerIdentity

    original = client.app.state.server_identity
    client.app.state.server_identity = ServerIdentity.build(verified_project="other")
    try:
        pinned = _preflight(client, payload)
    finally:
        client.app.state.server_identity = original
    assert pinned.status_code == 404, pinned.text
    assert pinned.json()["detail"] == "Project not found"
    assert _side_effects(client) == before


def _closed_loop_payload() -> dict[str, Any]:
    return {
        "workflow": "fake",
        "name": "b1-0-closed-loop",
        "input": {"source": "CCO"},
        "method": {"protocol": "ext"},
    }


def test_closed_loop_preflight_submit_status_view(client: TestClient) -> None:
    payload = _closed_loop_payload()
    preflight = _preflight(client, payload)
    assert preflight.status_code == 200, preflight.text
    fingerprint = preflight.json()["preflight_fingerprint"]
    assert isinstance(fingerprint, str) and fingerprint

    job_id = ""
    try:
        submitted = client.post(
            "/api/v1/jobs", json={**payload, "preflight_fingerprint": fingerprint}
        )
        assert submitted.status_code in (200, 201), submitted.text
        job_id = submitted.json()["job_id"]

        status = client.get(_STATUS.format(job_id=job_id, attempt=1))
        assert status.status_code == 200, status.text
        status_body = status.json()
        assert status_body["task_ref"]["job_id"] == job_id
        assert status_body["task_ref"]["attempt"] == 1

        view = client.get(_VIEW.format(job_id=job_id, attempt=1))
        assert view.status_code == 200, view.text
        assert view.json()["task_ref"]["job_id"] == job_id
        assert view.json()["task_ref"]["attempt"] == 1
    finally:
        if job_id:
            _cancel_and_wait(client, job_id)


def test_stale_fingerprint_is_rejected_before_side_effect(client: TestClient) -> None:
    original = _closed_loop_payload()
    pending = _preflight(client, original)
    assert pending.status_code == 200, pending.text
    stale = pending.json()["preflight_fingerprint"]

    mutated = _closed_loop_payload()
    mutated["input"] = {"source": "CCO", "charge": 1}
    before = _side_effects(client)
    response = client.post("/api/v1/jobs", json={**mutated, "preflight_fingerprint": stale})

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "preflight_stale"
    assert detail["params"]["field"] == "preflight_fingerprint"
    assert _side_effects(client) == before


# ---------------------------------------------------------------------------
# 6-7. View: legacy manifest and the remote mixed-cache matrix
# ---------------------------------------------------------------------------


def test_view_legacy_manifest_matches_frozen_schema(client: TestClient, tmp_path: Path) -> None:
    job_id = "b1-0-legacy-manifest"
    record = _seed_job(client, job_id=job_id, work_dir=tmp_path / "legacy", revision=1)

    response = client.get(
        _VIEW.format(job_id=job_id, attempt=1), params={"project_id": _project(client)}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provenance"]["source"] == "result_manifest"
    assert "release" not in body["task_ref"]
    assert set(body["task_ref"]) == {"job_id", "attempt", "task_id"}
    for artifact in body["artifacts"]:
        sha = artifact["sha256"]
        assert sha is None or re.fullmatch(r"[0-9a-f]{64}", sha), sha
    assert record.revision == 1
    assert body["manifest_generation"] != 2
    assert_matches(body, _load_schema("view-model"))


class _FakeResolver:
    def __init__(self, root: Any) -> None:
        self.root = root

    def resolve(self, job_id: str, attempt: int | None = None) -> Any:
        return self.root


def test_remote_mixed_cache_is_proven_at_route_level(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp_services.data.read_root import ReadRoot

    stale = tmp_path / "stale-work"
    _write_manifest(stale, product_id="stale_product")
    _write_receipt(stale, 2)
    job_id = "b1-0-remote-mixed"
    _seed_job(client, job_id=job_id, work_dir=stale, attempt=2, revision=1)

    resolver = _FakeResolver(None)
    import acp.api.v2_science as v2_science

    monkeypatch.setattr(
        v2_science,
        "build_read_root_resolver",
        lambda **_kwargs: resolver,
    )

    # (a) attempt-1 manifest sidecar + current release -> 200, no attempt-1 products.
    cache_a = tmp_path / "cache-a"
    _write_manifest(cache_a, product_id="attempt_one_product", sidecar_attempt=1)
    _write_receipt(cache_a, 2)
    resolver.root = ReadRoot(job_id=job_id, path=str(cache_a), is_remote=True, attempt=2)
    view_a = client.get(_VIEW.format(job_id=job_id, attempt=2))
    assert view_a.status_code == 200, view_a.text
    body_a = view_a.json()
    assert body_a["provenance"]["source"] == "execution_release"
    assert [item["artifact_id"] for item in body_a["artifacts"]] == []
    assert "attempt_one_product" not in json.dumps(body_a)
    assert "stale_product" not in json.dumps(body_a)

    # (b) current manifest + attempt-1 receipt -> 200 with products, not a release.
    cache_b = tmp_path / "cache-b"
    _write_manifest(cache_b, product_id="current_product", sidecar_attempt=2)
    _write_receipt(cache_b, 1)
    resolver.root = ReadRoot(job_id=job_id, path=str(cache_b), is_remote=True, attempt=2)
    view_b = client.get(_VIEW.format(job_id=job_id, attempt=2))
    assert view_b.status_code == 200, view_b.text
    body_b = view_b.json()
    assert body_b["provenance"]["source"] != "execution_release"
    assert [item["artifact_id"] for item in body_b["artifacts"]] == ["current_product"]
    assert "stale_product" not in json.dumps(body_b)

    # (c) both stale -> 409 with the frozen reason.
    cache_c = tmp_path / "cache-c"
    _write_manifest(cache_c, product_id="attempt_one_product", sidecar_attempt=1)
    _write_receipt(cache_c, 1)
    resolver.root = ReadRoot(job_id=job_id, path=str(cache_c), is_remote=True, attempt=2)
    view_c = client.get(_VIEW.format(job_id=job_id, attempt=2))
    assert view_c.status_code == 409, view_c.text
    detail = view_c.json()["detail"]
    assert detail["code"] == "resource_not_ready"
    assert detail["params"]["reason"] == "attempt_cache_identity_unverified"


# ---------------------------------------------------------------------------
# 8. Claims through the real prepare service
# ---------------------------------------------------------------------------

_EVIDENCE_REPORT: dict[str, Any] = {
    "schema": "acp2.science.evidence.v1",
    "task_ref": {"job_id": "b1-0-claims", "attempt": 1},
    "claims": [
        {
            "claim_id": "claim-b1-0",
            "statement": "The optimized geometry is a stationary point.",
            "status": "supported",
            "scope": {"subject": "system", "applicability": "system_specific"},
            "source_refs": [
                {"artifact_path": "RESULT/energy.json", "role": "native_output"},
                {"artifact_path": "RESULT/opt.xyz", "role": "geometry"},
            ],
            "missing_checks": [],
            "validator": "independent",
        }
    ],
    "limitations": [],
}


def _report_body() -> bytes:
    return json.dumps(_EVIDENCE_REPORT, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_claims_come_only_from_a_published_verified_report(
    client: TestClient, tmp_path: Path
) -> None:
    job_id = "b1-0-claims"
    _seed_job(client, job_id=job_id, work_dir=tmp_path / "claims", revision=1)
    operation = _enqueue_artifact(
        client,
        job_id=job_id,
        resource_id="evidence_report",
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )
    _publish_artifact(client, operation, _report_body())

    response = client.get(
        _VIEW.format(job_id=job_id, attempt=1), params={"project_id": _project(client)}
    )

    assert response.status_code == 200, response.text
    claims = response.json()["claims"]
    assert len(claims) == 1
    claim = claims[0]
    assert set(claim) == {"claim_id", "statement", "status", "evidence_refs", "reason"}
    assert claim["claim_id"] == "claim-b1-0"
    assert claim["status"] == "supported"
    assert claim["evidence_refs"] == ["RESULT/energy.json", "RESULT/opt.xyz"]
    assert claim["reason"] is None
    assert "source_refs" not in claim


def test_queued_operation_contributes_no_claim(client: TestClient, tmp_path: Path) -> None:
    job_id = "b1-0-claims-queued"
    _seed_job(client, job_id=job_id, work_dir=tmp_path / "claims-queued", revision=1)
    _enqueue_artifact(
        client,
        job_id=job_id,
        resource_id="evidence_report_queued",
        payload={"purpose": "artifact", "evidence_report": deepcopy(_EVIDENCE_REPORT)},
    )

    response = client.get(
        _VIEW.format(job_id=job_id, attempt=1), params={"project_id": _project(client)}
    )

    assert response.status_code == 200, response.text
    assert response.json()["claims"] == []


# ---------------------------------------------------------------------------
# 9. Artifact read bounds and refusals
# ---------------------------------------------------------------------------


def _seed_artifact(
    client: TestClient, tmp_path: Path, *, job_id: str, artifact_id: str = "artifact_read"
) -> tuple[str, Any]:
    _seed_job(client, job_id=job_id, work_dir=tmp_path / job_id, revision=1)
    operation = _enqueue_artifact(client, job_id=job_id, resource_id=artifact_id, generation=2)
    published = _publish_artifact(client, operation, b"prepared-artifact-body")
    return job_id, published


def test_artifact_read_is_bounded_and_verified(client: TestClient, tmp_path: Path) -> None:
    job_id, published = _seed_artifact(client, tmp_path, job_id="b1-0-artifact-read-bounds")
    project = _project(client)
    base = _ARTIFACT.format(job_id=job_id, attempt=1, artifact_id="artifact_read")

    full = client.get(
        base,
        params={
            "project_id": project,
            "generation": published.generation,
            "digest": published.digest,
        },
    )
    assert full.status_code == 200, full.text
    assert full.json()["body"] == "prepared-artifact-body"
    assert full.json()["truncated"] is False

    bounded = client.get(
        base,
        params={
            "project_id": project,
            "generation": published.generation,
            "digest": published.digest,
            "max_bytes": 4,
        },
    )
    assert bounded.status_code == 200, bounded.text
    assert bounded.json()["truncated"] is True
    assert len(bounded.json()["body"]) == 4


def test_artifact_read_refuses_wrong_digest_and_stale_generation(
    client: TestClient, tmp_path: Path
) -> None:
    job_id, published = _seed_artifact(client, tmp_path, job_id="b1-0-artifact-read-refusals")
    project = _project(client)
    base = _ARTIFACT.format(job_id=job_id, attempt=1, artifact_id="artifact_read")

    wrong_digest = client.get(
        base,
        params={
            "project_id": project,
            "generation": published.generation,
            "digest": "sha256:" + "f" * 64,
        },
    )
    assert wrong_digest.status_code == 409, wrong_digest.text
    assert wrong_digest.json()["detail"]["code"] == "revision_conflict"
    assert wrong_digest.json()["detail"]["params"]["rule"] == "digest_mismatch"

    stale = client.get(
        base,
        params={
            "project_id": project,
            "generation": 1,
            "digest": published.digest,
        },
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "revision_conflict"
    assert stale.json()["detail"]["params"]["rule"] == "generation_stale"


# ---------------------------------------------------------------------------
# 10. Research documents through the lifespan store
# ---------------------------------------------------------------------------


def test_proposal_read_round_trips_and_validates(client: TestClient) -> None:
    documents = _ensure_research_documents(client)
    project = _project(client)

    response = client.get(
        _PROPOSAL.format(proposal_id=_PROPOSAL_ID), params={"project_id": project}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == documents["proposal"]
    assert body["proposal_id"] == _PROPOSAL_ID
    assert _PROPOSAL_FORBIDDEN_KEYS.isdisjoint(body)
    assert_matches(body, _load_schema("proposal"))


def test_record_read_round_trips_and_validates(client: TestClient) -> None:
    documents = _ensure_research_documents(client)
    project = _project(client)

    response = client.get(_RECORD.format(research_id=_RECORD_ID), params={"project_id": project})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == documents["record"]
    assert body["research_id"] == _RECORD_ID
    assert_matches(body, _load_schema("research-record"))


def test_research_read_pinned_project_mismatch_is_404(client: TestClient) -> None:
    _ensure_research_documents(client)
    project = _project(client)
    from acp_services.workspace.server_identity import ServerIdentity

    original = client.app.state.server_identity
    client.app.state.server_identity = ServerIdentity.build(verified_project="other")
    try:
        proposal = client.get(
            _PROPOSAL.format(proposal_id=_PROPOSAL_ID), params={"project_id": project}
        )
        record = client.get(_RECORD.format(research_id=_RECORD_ID), params={"project_id": project})
    finally:
        client.app.state.server_identity = original

    for response in (proposal, record):
        assert response.status_code == 404, response.text
        assert response.json() == {"detail": "Project not found"}


# ---------------------------------------------------------------------------
# 11. One zero-side-effect read sweep
# ---------------------------------------------------------------------------


def test_read_sweep_has_zero_side_effects(client: TestClient, tmp_path: Path) -> None:
    job_id, published = _seed_artifact(client, tmp_path, job_id="b1-0-artifact-read-sweep")
    documents = _ensure_research_documents(client)
    project = _project(client)
    before = _side_effects(client)

    assert client.get(_CAPABILITIES, params={"project_id": project}).status_code == 200
    assert client.get(_STATUS.format(job_id=job_id, attempt=1)).status_code == 200
    view = client.get(_VIEW.format(job_id=job_id, attempt=1), params={"project_id": project})
    assert view.status_code == 200
    assert (
        client.get(
            _ARTIFACT.format(job_id=job_id, attempt=1, artifact_id="artifact_read"),
            params={
                "project_id": project,
                "generation": published.generation,
                "digest": published.digest,
            },
        ).status_code
        == 200
    )
    proposal = client.get(
        _PROPOSAL.format(proposal_id=documents["proposal"]["proposal_id"]),
        params={"project_id": project},
    )
    record = client.get(
        _RECORD.format(research_id=documents["record"]["research_id"]),
        params={"project_id": project},
    )
    assert proposal.status_code == 200
    assert record.status_code == 200

    assert _side_effects(client) == before


# ---------------------------------------------------------------------------
# 12. The validator itself is not a rubber stamp
# ---------------------------------------------------------------------------


def test_validator_rejects_a_view_without_provenance(client: TestClient, tmp_path: Path) -> None:
    job_id = "b1-0-validator"
    _seed_job(client, job_id=job_id, work_dir=tmp_path / "validator", revision=1)
    response = client.get(
        _VIEW.format(job_id=job_id, attempt=1), params={"project_id": _project(client)}
    )
    assert response.status_code == 200, response.text
    schema = _load_schema("view-model")
    assert_matches(response.json(), schema)

    broken = deepcopy(response.json())
    broken.pop("provenance")
    with pytest.raises(AssertionError, match="missing required property 'provenance'"):
        assert_matches(broken, schema)
