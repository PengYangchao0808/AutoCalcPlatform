"""B1-7 — read-only research proposals / records projections.

These tests pin the two additive Workbench read routes:

* ``GET /api/v2/research/proposals/{proposal_id}``
* ``GET /api/v2/research/records/{research_id}``

The router is mounted post-hoc on the live app because ``server.py`` mounting is
owned by a later wave; the store is injected at ``app.state.research_resources``
because the lifespan wiring lands in the same later wave.  Every assertion is
made through the real HTTP surface of :func:`acp.api.server.create_app`.

Contract facts pinned here:

1. a 200 body is the stored document verbatim (no envelope, no added status /
   lifecycle / job fields);
2. the gate order refuses a missing ``project_id`` with 400, a pinned-project
   mismatch with 404, an unknown / cross-project / revoked row with 404;
3. the DB ``schema`` column (not ``document["schema"]``) is authoritative — a
   tampered column is a generic 500 with no traceback text;
4. a record ``task_refs[].project_id`` crossing the resource project is the
   named 409 ``revision_conflict`` / ``cross_project_task_ref``;
5. the same document id under both kinds and under two projects never collides.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.data.research_resources import (
    PROPOSAL_SCHEMA,
    RESEARCH_RECORD_SCHEMA,
    ResearchResourceRequest,
    ResearchResourceStore,
    research_resource_key,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURES = _REPO_ROOT / "docs/reference/next-change/acp2-contracts/experience/fixtures"
_PROPOSAL_FIXTURE = _FIXTURES / "proposal.valid.json"
_RECORD_FIXTURE = _FIXTURES / "research-record.valid.json"

_PROPOSAL_REQUIRED = (
    "proposal_id",
    "project_id",
    "question_ref",
    "question_revision",
    "reaction_ref",
    "reaction_revision",
    "hypothesis",
    "recipe_digest",
    "plan_definition_digest",
    "science",
    "budget",
    "approval_ref",
    "allowed_change_scope",
    "round",
    "decision",
    "created_at",
    "human_edits",
)
_FORBIDDEN_ADDED_KEYS = frozenset({"status", "state", "lifecycle", "job_id", "attempt"})

_PROPOSAL_PATH = "/api/v2/research/proposals/{proposal_id}"
_RECORD_PATH = "/api/v2/research/records/{research_id}"

project = "proj-a"
other_project = "proj-b"
principal = "principal-local"


def _set_environment(monkeypatch: pytest.MonkeyPatch, run_root: Path) -> None:
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_RUN_ROOT", str(run_root))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_MAX_RUNNING", "1")
    monkeypatch.delenv("ACP_SERVER_PROJECT", raising=False)


def _ensure_project(client: TestClient, project_id: str) -> None:
    """Register a project so the server-side project claim resolves."""
    manager = client.app.state.job_manager
    if manager.projects.get_project(project_id) is not None:
        return
    manager.projects._create_with_id(
        project_id,
        name=f"Project {project_id}",
        description="",
        tags=[],
        settings={},
    )


@pytest.fixture()
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[TestClient, ResearchResourceStore], None, None]:
    _set_environment(monkeypatch, tmp_path)
    from acp.api import v2_research
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as test_client:
        test_client.app.include_router(v2_research.router, prefix="/api/v2")
        test_client.app.openapi_schema = None
        store = ResearchResourceStore(tmp_path / "acp_research.db")
        test_client.app.state.research_resources = store
        _ensure_project(test_client, project)
        _ensure_project(test_client, other_project)
        try:
            yield test_client, store
        finally:
            store.close()


def _load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _proposal_document(
    *, project_id: str = project, proposal_id: str = "prop-http-001", **overrides: Any
) -> dict[str, Any]:
    document = _load_fixture(_PROPOSAL_FIXTURE)
    document["project_id"] = project_id
    document["proposal_id"] = proposal_id
    document.update(overrides)
    return document


def _record_document(
    *, project_id: str = project, research_id: str = "res-http-001", **overrides: Any
) -> dict[str, Any]:
    document = _load_fixture(_RECORD_FIXTURE)
    document["project_id"] = project_id
    document["research_id"] = research_id
    for task_ref in document["task_refs"]:
        task_ref["project_id"] = project_id
    document.update(overrides)
    return document


def _kind_and_id(document: dict[str, Any], kind: str) -> tuple[str, str]:
    if kind == "proposal":
        return PROPOSAL_SCHEMA, str(document["proposal_id"])
    return RESEARCH_RECORD_SCHEMA, str(document["research_id"])


def _seed(
    store: ResearchResourceStore,
    document: dict[str, Any],
    *,
    kind: str,
    project_id: str = project,
) -> str:
    schema, document_id = _kind_and_id(document, kind)
    key = research_resource_key(project_id, kind, document_id)
    store.create(
        ResearchResourceRequest(
            resource_id=key,
            schema=schema,
            document=document,
            principal_id=principal,
            project_id=project_id,
        )
    )
    return key


def _pin(client: TestClient, project_id: str | None) -> None:
    from acp_services.workspace.server_identity import ServerIdentity

    client.app.state.server_identity = ServerIdentity.build(verified_project=project_id)


def _get_proposal(client: TestClient, proposal_id: str, project_id: str | None) -> Any:
    params = {} if project_id is None else {"project_id": project_id}
    return client.get(f"/api/v2/research/proposals/{proposal_id}", params=params)


def _get_record(client: TestClient, research_id: str, project_id: str | None) -> Any:
    params = {} if project_id is None else {"project_id": project_id}
    return client.get(f"/api/v2/research/records/{research_id}", params=params)


def _update_row(
    store: ResearchResourceStore,
    resource_id: str,
    *,
    schema: str | None = None,
    document: dict[str, Any] | None = None,
) -> None:
    """Parameterized raw patch of exactly one row (never another row)."""
    assignments: list[str] = []
    values: list[Any] = []
    if schema is not None:
        assignments.append("schema = ?")
        values.append(schema)
    if document is not None:
        assignments.append("document_json = ?")
        values.append(json.dumps(document, sort_keys=True, separators=(",", ":")))
    if not assignments:
        raise AssertionError("nothing to update")
    with sqlite3.connect(str(store.db_path)) as conn:
        conn.execute(
            f"UPDATE research_resources SET {', '.join(assignments)} "
            "WHERE resource_id = ? AND revision = ?",
            (*values, resource_id, 1),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# 1-2. happy paths
# ---------------------------------------------------------------------------


def test_proposal_read_returns_stored_document_verbatim(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    document = _proposal_document()
    _seed(store, document, kind="proposal")

    response = _get_proposal(test_client, document["proposal_id"], project)

    assert response.status_code == 200
    body = response.json()
    assert body == document
    assert set(_PROPOSAL_REQUIRED) <= set(body)
    assert _FORBIDDEN_ADDED_KEYS.isdisjoint(body)
    assert "schema" not in body


def test_record_read_returns_stored_document_verbatim(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    document = _record_document()
    _seed(store, document, kind="record")

    response = _get_record(test_client, document["research_id"], project)

    assert response.status_code == 200
    body = response.json()
    assert body == document
    assert body["research_id"] == document["research_id"]
    assert _FORBIDDEN_ADDED_KEYS.isdisjoint(body)


# ---------------------------------------------------------------------------
# 3. missing / revoked
# ---------------------------------------------------------------------------


def test_missing_ids_are_404(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, _store = client
    _pin(test_client, project)

    proposal = _get_proposal(test_client, "prop-does-not-exist", project)
    record = _get_record(test_client, "res-does-not-exist", project)

    assert proposal.status_code == 404
    assert record.status_code == 404
    assert proposal.json() == {"detail": "Research resource not found"}
    assert record.json() == {"detail": "Research resource not found"}


def test_revoked_resource_is_404(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    document = _proposal_document(proposal_id="prop-revoked")
    key = _seed(store, document, kind="proposal")
    store.revoke(
        key,
        principal_id=principal,
        project_id=project,
        reason="test revocation",
    )

    response = _get_proposal(test_client, document["proposal_id"], project)

    assert response.status_code == 404
    assert response.json() == {"detail": "Research resource not found"}


# ---------------------------------------------------------------------------
# 4-5. gate order
# ---------------------------------------------------------------------------


def test_pinned_project_mismatch_is_404(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    document = _proposal_document(project_id=other_project, proposal_id="prop-other")
    _seed(store, document, kind="proposal", project_id=other_project)
    _pin(test_client, project)

    response = _get_proposal(test_client, document["proposal_id"], other_project)

    assert response.status_code == 404
    assert response.json() == {"detail": "Project not found"}


def test_project_id_query_is_required(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, _store = client
    _pin(test_client, project)

    missing = _get_proposal(test_client, "prop-x", None)
    blank = _get_proposal(test_client, "prop-x", "   ")
    record_missing = _get_record(test_client, "res-x", None)

    for response in (missing, blank, record_missing):
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["code"] == "input_invalid"
        assert detail["message"] == "project_id is required"
        assert detail["params"] == {"field": "project_id"}


# ---------------------------------------------------------------------------
# 6-7. tampered rows
# ---------------------------------------------------------------------------


def test_schema_column_mismatch_is_generic_500(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    document = _proposal_document(proposal_id="prop-schema-tamper")
    key = _seed(store, document, kind="proposal")
    _update_row(store, key, schema=RESEARCH_RECORD_SCHEMA)

    response = _get_proposal(test_client, document["proposal_id"], project)

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "internal_error"
    assert detail["message"] == "research resource schema mismatch"
    lowered = response.text.lower()
    assert "traceback" not in lowered
    assert "sqlite" not in lowered
    assert "research_resources" not in lowered


def test_record_task_ref_cross_project_is_409(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    document = _record_document(research_id="res-cross-ref")
    key = _seed(store, document, kind="record")
    tampered = {
        **document,
        "task_refs": [
            {**task_ref, "project_id": other_project} for task_ref in document["task_refs"]
        ],
    }
    _update_row(store, key, document=tampered)

    response = _get_record(test_client, document["research_id"], project)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["message"] == "research record task_refs cross project scope"
    assert detail["params"] == {"rule": "cross_project_task_ref"}


# ---------------------------------------------------------------------------
# 8-10. identity isolation
# ---------------------------------------------------------------------------


def test_same_document_id_in_both_kinds_is_distinct(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    shared_id = "shared-doc-1"
    proposal = _proposal_document(proposal_id=shared_id)
    record = _record_document(research_id=shared_id)
    proposal_key = _seed(store, proposal, kind="proposal")
    record_key = _seed(store, record, kind="record")

    assert proposal_key != record_key
    proposal_response = _get_proposal(test_client, shared_id, project)
    record_response = _get_record(test_client, shared_id, project)

    assert proposal_response.status_code == 200
    assert record_response.status_code == 200
    assert proposal_response.json() == proposal
    assert record_response.json() == record


def test_same_id_in_two_projects_reads_each_own_document(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    shared_id = "prop-shared-across-projects"
    own = _proposal_document(
        project_id=project,
        proposal_id=shared_id,
        display_name="project-a copy",
    )
    other = _proposal_document(
        project_id=other_project,
        proposal_id=shared_id,
        display_name="project-b copy",
    )
    _seed(store, own, kind="proposal", project_id=project)
    _seed(store, other, kind="proposal", project_id=other_project)

    _pin(test_client, project)
    own_response = _get_proposal(test_client, shared_id, project)
    _pin(test_client, other_project)
    other_response = _get_proposal(test_client, shared_id, other_project)

    assert own_response.status_code == 200
    assert other_response.status_code == 200
    assert own_response.json()["project_id"] == project
    assert own_response.json()["display_name"] == "project-a copy"
    assert other_response.json()["project_id"] == other_project
    assert other_response.json()["display_name"] == "project-b copy"
    assert own_response.json() != other_response.json()


def test_128_character_document_id_is_readable(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, store = client
    _pin(test_client, project)
    long_id = "x" * 128
    document = _proposal_document(proposal_id=long_id)
    _seed(store, document, kind="proposal")

    response = _get_proposal(test_client, long_id, project)

    assert response.status_code == 200
    assert response.json() == document


# ---------------------------------------------------------------------------
# 11. openapi templates
# ---------------------------------------------------------------------------


def test_openapi_exposes_both_exact_read_templates(
    client: tuple[TestClient, ResearchResourceStore],
) -> None:
    test_client, _store = client

    schema = test_client.get("/openapi.json").json()
    paths = schema["paths"]

    assert _PROPOSAL_PATH in paths
    assert _RECORD_PATH in paths
    proposal_params = paths[_PROPOSAL_PATH]["get"]["parameters"]
    record_params = paths[_RECORD_PATH]["get"]["parameters"]
    assert any(
        param["name"] == "proposal_id" and param["in"] == "path" for param in proposal_params
    )
    assert any(param["name"] == "research_id" and param["in"] == "path" for param in record_params)
    assert any(
        param["name"] == "project_id" and param["in"] == "query" and param["required"] is False
        for param in proposal_params
    )
