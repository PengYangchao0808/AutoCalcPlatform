"""Tests for the project chemistry network (B0 structure versions + B1 identity).

Contract: ``docs/reference/next-change/ACP_Chemistry_Network_Contract_20261103.md``
(§2 structure-version identity, §3 closed edge enum, §4 wire shape + node merge
rules, §5 no side effects, §7 forbidden name/SMILES merging).

Two separated groups:

* **pure projection** — imports only ``acp.api.chemistry_projection`` and
  ``acp.api.v2_schemas``; never ``acp.api.server``.
* **route** — a live ``TestClient`` over ``create_app``.  These cannot run on
  Windows (``acp_services.runtime.attempt_freeze`` imports ``fcntl``); the client
  fixture records that platform limitation as a skip, so CI is the only place
  they execute.
"""

from __future__ import annotations

import itertools
import json
import sqlite3
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from acp.api.chemistry_projection import (
    EDGE_RELATIONS,
    IDENTITY_KIND_MIXED,
    MOLECULE_IDENTITY_KIND,
    NODE_IDENTITY_KIND,
    SKIP_IDENTITY_METADATA_MISSING,
    SKIP_MISSING_STRUCTURE_HASH,
    SKIP_ORPHAN_ENDPOINT,
    build_network,
)
from acp.api.v2_schemas import V2ChemistryNetworkNode, V2ChemistryNetworkResponse

_CHECKSUM_A = "sha256:" + "a" * 64
_CHECKSUM_B = "sha256:" + "b" * 64
_CHECKSUM_C = "sha256:" + "c" * 64

_MOL_KEY_1 = "mol_" + "1" * 24
_MOL_KEY_2 = "mol_" + "2" * 24
_SCHEME = "mol_identity_v1"


# ── pure projection group ──────────────────────────────────────────────


def _task_row(task_id: str, molecule_name: str, molecule_key: str = "") -> dict[str, Any]:
    return {
        "task_id": task_id,
        "job_id": task_id,
        "project_id": "proj",
        "molecule_name": molecule_name,
        "molecule_key": molecule_key,
        "input_hash": "",
    }


def _source_row(
    job_id: str,
    checksum: str,
    *,
    name: str = "",
    formula: str = "",
    charge: Any = None,
    atom_count: Any = None,
    identity_key: str = "",
) -> dict[str, Any]:
    return {
        "source_uid": f"ss_{job_id}_{(checksum or 'none')[-6:]}",
        "job_id": job_id,
        "relative_path": "RESULT/input.xyz",
        "content_checksum": checksum,
        "molecule_identity_key": identity_key,
        "formula": formula,
        "atom_count": atom_count,
        "charge": charge,
        "molecule_name": name,
    }


def _identity_row(
    identity_key: str,
    *,
    scheme: str = _SCHEME,
    canonical_smiles: str = "CCO",
    charge: Any = 0,
    multiplicity: Any = 1,
    formula: str = "C2H6O",
    inchikey: str = "LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
) -> dict[str, Any]:
    """One ``structure_identities`` registry row (the shape the route reads)."""
    return {
        "identity_key": identity_key,
        "scheme": scheme,
        "canonical_smiles": canonical_smiles,
        "charge": charge,
        "multiplicity": multiplicity,
        "formula": formula,
        "inchikey": inchikey,
    }


def _project(
    sources: list[dict[str, Any]],
    tasks: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    *,
    limit: int = 200,
    truncated: bool = False,
    next_cursor: str | None = None,
    identities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return build_network(
        "proj",
        tasks=tasks if tasks is not None else [],
        sources=sources,
        edges_raw=edges if edges is not None else [],
        limit=limit,
        truncated=truncated,
        next_cursor=next_cursor,
        identities=identities,
    )


def test_projection_merges_same_checksum_into_one_node() -> None:
    payload = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="ethanol", formula="C2H6O", charge=0),
            _source_row("t2", _CHECKSUM_A, name="ethanol", atom_count=9),
        ]
    )

    assert len(payload["nodes"]) == 1
    node = payload["nodes"][0]
    assert node["node_id"] == _CHECKSUM_A
    assert node["identity_kind"] == NODE_IDENTITY_KIND
    assert node["task_ids"] == ["t1", "t2"]
    assert node["formula"] == "C2H6O"
    assert node["charge"] == 0
    assert node["atom_count"] == 9
    assert payload["identity_kind"] == NODE_IDENTITY_KIND
    assert payload["skipped"] == []


def test_projection_keeps_different_checksums_as_two_nodes() -> None:
    payload = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="ethanol"),
            _source_row("t2", _CHECKSUM_B, name="ethanol"),
        ]
    )

    assert [node["node_id"] for node in payload["nodes"]] == [_CHECKSUM_A, _CHECKSUM_B]
    assert all(len(node["task_ids"]) == 1 for node in payload["nodes"])


def test_projection_skips_missing_structure_hash() -> None:
    for missing in (None, "", "   "):
        payload = _project([_source_row("t1", missing, name="invented-from-name")])

        assert payload["nodes"] == []
        assert payload["skipped"] == [{"reason": SKIP_MISSING_STRUCTURE_HASH, "task_id": "t1"}]


def test_projection_never_merges_by_display_name() -> None:
    # Same version, different display names → still one node.
    merged = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="ethanol"),
            _source_row("t2", _CHECKSUM_A, name="EtOH"),
        ]
    )
    assert len(merged["nodes"]) == 1
    assert set(merged["nodes"][0]["molecule_names"]) == {"ethanol", "EtOH"}

    # Same display name, different versions → never one node (§7.1/§7.2).
    split = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="ethanol"),
            _source_row("t2", _CHECKSUM_B, name="ethanol"),
        ]
    )
    assert len(split["nodes"]) == 2


def test_projection_ignores_molecule_key() -> None:
    baseline = _project(
        [_source_row("t1", _CHECKSUM_A), _source_row("t2", _CHECKSUM_B)],
        [_task_row("t1", "ethanol"), _task_row("t2", "ethanol")],
    )
    conflicting = _project(
        [_source_row("t1", _CHECKSUM_A), _source_row("t2", _CHECKSUM_B)],
        [
            _task_row("t1", "ethanol", molecule_key="water"),
            _task_row("t2", "ethanol", molecule_key="water"),
        ],
    )

    assert conflicting == baseline


def test_projection_groups_identified_sources_by_identity_key() -> None:
    payload = _project(
        [
            _source_row(
                "t1",
                _CHECKSUM_A,
                name="ethanol",
                formula="C2H6O",
                atom_count=9,
                identity_key=_MOL_KEY_1,
            ),
            _source_row("t2", _CHECKSUM_B, name="EtOH", identity_key=_MOL_KEY_1),
        ],
        identities=[_identity_row(_MOL_KEY_1)],
    )

    assert len(payload["nodes"]) == 1
    node = payload["nodes"][0]
    assert node["node_id"] == _MOL_KEY_1
    assert node["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert node["scheme"] == _SCHEME
    assert node["task_ids"] == ["t1", "t2"]
    assert node["canonical_smiles"] == "CCO"
    assert node["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert node["charge"] == 0
    assert node["multiplicity"] == 1
    assert node["formula"] == "C2H6O"
    assert node["atom_count"] == 9
    assert set(node) == set(V2ChemistryNetworkNode.model_fields)
    assert payload["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert payload["identified_nodes"] == 1
    assert payload["unidentified_nodes"] == 0


def test_projection_never_merges_identified_and_unidentified_same_checksum() -> None:
    payload = _project(
        [
            _source_row("t1", _CHECKSUM_A, identity_key=_MOL_KEY_1),
            _source_row("t2", _CHECKSUM_A),
        ],
        identities=[_identity_row(_MOL_KEY_1)],
    )

    assert len(payload["nodes"]) == 2
    assert [node["node_id"] for node in payload["nodes"]] == [_MOL_KEY_1, _CHECKSUM_A]
    kinds = {node["node_id"]: node["identity_kind"] for node in payload["nodes"]}
    assert kinds[_MOL_KEY_1] == MOLECULE_IDENTITY_KIND
    assert kinds[_CHECKSUM_A] == NODE_IDENTITY_KIND
    assert payload["identity_kind"] == IDENTITY_KIND_MIXED
    assert payload["identified_nodes"] == 1
    assert payload["unidentified_nodes"] == 1


def test_projection_all_unidentified_project_is_structure_content_v1() -> None:
    payload = _project(
        [_source_row("t1", _CHECKSUM_A), _source_row("t2", _CHECKSUM_B)],
        identities=[_identity_row(_MOL_KEY_1)],
    )

    assert payload["identity_kind"] == NODE_IDENTITY_KIND
    assert payload["identified_nodes"] == 0
    assert payload["unidentified_nodes"] == 2
    assert {node["identity_kind"] for node in payload["nodes"]} == {NODE_IDENTITY_KIND}
    assert all(node["scheme"] == "" for node in payload["nodes"])
    assert all(node["canonical_smiles"] == "" for node in payload["nodes"])


def test_projection_all_identified_project_is_molecule_v1() -> None:
    payload = _project(
        [
            _source_row("t1", _CHECKSUM_A, identity_key=_MOL_KEY_1),
            _source_row("t2", _CHECKSUM_B, identity_key=_MOL_KEY_2),
        ],
        identities=[_identity_row(_MOL_KEY_1), _identity_row(_MOL_KEY_2, canonical_smiles="O")],
    )

    assert payload["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert payload["identified_nodes"] == 2
    assert payload["unidentified_nodes"] == 0
    assert [node["node_id"] for node in payload["nodes"]] == [_MOL_KEY_1, _MOL_KEY_2]
    assert payload["skipped"] == []


def test_projection_uses_registry_metadata_only() -> None:
    payload = _project(
        [
            # Source rows claim different descriptive facts: the registry wins for
            # molecule metadata, source rows only keep formula/atom_count.
            _source_row(
                "t1", _CHECKSUM_A, formula="C2H6O", charge=2, atom_count=9, identity_key=_MOL_KEY_1
            ),
            _source_row("t2", _CHECKSUM_B, formula="", charge=None, identity_key=_MOL_KEY_1),
        ],
        identities=[
            _identity_row(
                _MOL_KEY_1,
                scheme=_SCHEME,
                canonical_smiles="CCO",
                charge=-1,
                multiplicity=2,
                inchikey="LFQSCWFLJHTTHZ-UHFFFAOYSA-N",
            )
        ],
    )

    node = payload["nodes"][0]
    assert node["scheme"] == _SCHEME
    assert node["canonical_smiles"] == "CCO"
    assert node["charge"] == -1
    assert node["multiplicity"] == 2
    assert node["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert node["formula"] == "C2H6O"
    assert node["atom_count"] == 9


def test_projection_identified_source_without_registry_row_keeps_node_and_skips() -> None:
    payload = _project(
        [
            _source_row("t1", _CHECKSUM_A, formula="C2H6O", identity_key=_MOL_KEY_1),
            _source_row("t2", _CHECKSUM_B, identity_key=_MOL_KEY_2),
        ],
        identities=[_identity_row(_MOL_KEY_1)],
    )

    # Both identified nodes survive: a missing registry row never drops or
    # re-keys the node, it only leaves the metadata empty.
    assert [node["node_id"] for node in payload["nodes"]] == [_MOL_KEY_1, _MOL_KEY_2]
    orphan = payload["nodes"][1]
    assert orphan["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert orphan["scheme"] == ""
    assert orphan["canonical_smiles"] == ""
    assert orphan["inchikey"] == ""
    assert orphan["charge"] is None
    assert orphan["multiplicity"] is None
    assert payload["skipped"] == [{"reason": SKIP_IDENTITY_METADATA_MISSING, "task_id": "t2"}]
    assert payload["identity_kind"] == MOLECULE_IDENTITY_KIND


def test_projection_identity_is_never_derived_from_names_or_molecule_key() -> None:
    # Conflicting display names/keys everywhere: only the persisted identity key
    # can merge or split nodes (§7.1/§7.2).
    baseline = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="ethanol", identity_key=_MOL_KEY_1),
            _source_row("t2", _CHECKSUM_B, name="ethanol", identity_key=_MOL_KEY_2),
        ],
        [_task_row("t1", "ethanol", molecule_key="water"), _task_row("t2", "water")],
        identities=[_identity_row(_MOL_KEY_1), _identity_row(_MOL_KEY_2)],
    )
    conflicting = _project(
        [
            _source_row("t1", _CHECKSUM_A, name="water", identity_key=_MOL_KEY_1),
            _source_row("t2", _CHECKSUM_B, name="methane", identity_key=_MOL_KEY_2),
        ],
        [
            _task_row("t1", "methane", molecule_key="ethanol"),
            _task_row("t2", "ethanol", molecule_key="ethanol"),
        ],
        identities=[_identity_row(_MOL_KEY_1), _identity_row(_MOL_KEY_2)],
    )

    assert len(baseline["nodes"]) == 2
    assert [n["node_id"] for n in baseline["nodes"]] == [n["node_id"] for n in conflicting["nodes"]]
    assert [n["task_ids"] for n in baseline["nodes"]] == [
        n["task_ids"] for n in conflicting["nodes"]
    ]
    assert [n["charge"] for n in baseline["nodes"]] == [n["charge"] for n in conflicting["nodes"]]


def test_projection_node_cap_is_deterministic_and_truncated() -> None:
    sources = [
        _source_row("t3", _CHECKSUM_C),
        _source_row("t1", _CHECKSUM_A),
        _source_row("t2", _CHECKSUM_B),
    ]

    payload = _project(sources, limit=2, next_cursor="3")

    assert [node["node_id"] for node in payload["nodes"]] == [_CHECKSUM_A, _CHECKSUM_B]
    assert payload["truncated"] is True
    assert payload["next_cursor"] == "3"


def test_projection_edge_dedupe_self_edge_and_orphan() -> None:
    payload = _project(
        [_source_row("a", _CHECKSUM_A), _source_row("b", _CHECKSUM_B)],
        edges=[
            {"from_task": "a", "to_task": "b", "relation": "lineage", "depth": 1},
            {"from_task": "a", "to_task": "b", "relation": "lineage", "depth": 1},
            {"from_task": "a", "to_task": "a", "relation": "lineage", "depth": 1},
            {"from_task": "a", "to_task": "ghost", "relation": "lineage", "depth": 1},
            {"from_task": "a", "to_task": "b", "relation": "pes_triplet", "depth": 1},
        ],
    )

    assert payload["edges"] == [
        {"relation": "lineage", "from_task": "a", "to_task": "b", "depth": 1}
    ]
    assert payload["skipped"] == [{"reason": SKIP_ORPHAN_ENDPOINT, "task_id": "ghost"}]
    assert EDGE_RELATIONS == ("lineage",)


def test_projection_edge_budget_truncates() -> None:
    sources = [_source_row(f"t{i}", f"sha256:{i:064x}") for i in range(6)]
    edges = [
        {"from_task": source, "to_task": target, "relation": "lineage", "depth": 1}
        for source, target in itertools.permutations([f"t{i}" for i in range(6)], 2)
    ]

    # limit=6 → node budget 6, edge budget 4 * 6 = 24 of the 30 distinct pairs.
    payload = _project(sources, edges=edges, limit=6)

    assert len(payload["edges"]) == 24
    assert payload["truncated"] is True


def test_schema_validates_projection_payload() -> None:
    payload = _project(
        [_source_row("t1", _CHECKSUM_A, name="ethanol", formula="C2H6O", identity_key=_MOL_KEY_1)],
        [_task_row("t1", "ethanol")],
        [{"from_task": "t1", "to_task": "t1", "relation": "lineage"}],
        identities=[_identity_row(_MOL_KEY_1)],
    )

    model = V2ChemistryNetworkResponse.model_validate(payload)

    assert set(payload) == set(V2ChemistryNetworkResponse.model_fields)
    assert set(payload["nodes"][0]) == set(V2ChemistryNetworkNode.model_fields)
    assert model.project_id == "proj"
    assert model.identity_kind == MOLECULE_IDENTITY_KIND
    assert model.identified_nodes == 1
    assert model.unidentified_nodes == 0
    assert model.nodes[0].node_id == _MOL_KEY_1
    assert model.nodes[0].scheme == _SCHEME
    assert model.nodes[0].multiplicity == 1
    assert model.model_dump()["next_cursor"] is None

    # The B0 payload shape still validates unchanged.
    b0 = V2ChemistryNetworkResponse.model_validate(
        _project([_source_row("t1", _CHECKSUM_A, name="ethanol", formula="C2H6O")])
    )
    assert b0.identity_kind == NODE_IDENTITY_KIND
    assert b0.nodes[0].node_id == _CHECKSUM_A
    assert b0.nodes[0].scheme == ""


# ── route group (CI-only on this checkout: Windows has no fcntl) ───────

_NETWORK = "/api/v2/projects/{project_id}/chemistry-network"


def _load_create_app() -> Any:
    try:
        from acp.api.server import create_app
    except ImportError as exc:  # pragma: no cover - platform limitation
        if sys.platform == "win32" and "fcntl" in str(exc):
            pytest.skip(f"acp.api.server needs fcntl (POSIX only): {exc}")
        raise
    return create_app


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Any, None, None]:
    import unittest.mock as mock

    from fastapi.testclient import TestClient

    create_app = _load_create_app()
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    monkeypatch.setenv("ACP_DEPLOYMENT_TIER", "local-single")
    monkeypatch.setenv("ACP_PUBLIC_ORIGIN", "")
    monkeypatch.setenv("ACP_MAX_RUNNING", "2")
    with mock.patch("acp.scheduler.capabilities.local_satisfies", return_value=True):
        with TestClient(create_app(run_root=tmp_path, max_running=2)) as test_client:
            yield test_client


def _get(client: Any, url: str) -> Any:
    manager = client.app.state.job_manager
    before = manager.store.counts()
    response = client.get(url)
    after = manager.store.counts()
    assert after == before, f"GET {url} changed store counts: {before} -> {after}"
    return response


def _default_project_id(client: Any) -> str:
    response = client.get("/api/v2/projects")
    assert response.status_code == 200, response.text
    for project in response.json():
        if project["name"] == "Uncategorized":
            return str(project["project_id"])
    raise AssertionError("default project missing")


def _batch_create(client: Any, project_id: str, names: list[str]) -> list[str]:
    payload: dict[str, Any] = {
        "project_id": project_id,
        "tasks": [
            {
                "molecule_name": name,
                "task_name": f"conf_{index}",
                "workflow": "Confsearch",
                "input": {"source": "CCO"},
            }
            for index, name in enumerate(names)
        ],
    }
    response = client.post("/api/v2/tasks/batch", json=payload)
    assert response.status_code == 201, response.text
    created = [item["task_id"] for item in response.json()["created"]]
    assert len(created) == len(names), response.text
    return created


def _wait_for_terminal(client: Any, task_ids: list[str], timeout: float = 10.0) -> None:
    """Wait until the poller stops rewriting rows before touching spec_json."""
    deadline = time.monotonic() + timeout
    db_path = client.app.state.job_manager.store.db_path
    terminal = {"completed", "failed", "cancelled"}
    while time.monotonic() < deadline:
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT status FROM jobs WHERE id IN ({})".format(",".join("?" for _ in task_ids)),
                task_ids,
            ).fetchall()
        if rows and all(row[0] in terminal for row in rows):
            return
        time.sleep(0.05)


def _rewrite_spec_json(client: Any, task_id: str, new_input: dict[str, Any]) -> None:
    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT spec_json FROM jobs WHERE id=?", (task_id,)).fetchone()
        assert row is not None
        spec = json.loads(row[0])
        spec["input"] = new_input
        conn.execute("UPDATE jobs SET spec_json=? WHERE id=?", (json.dumps(spec), task_id))
        conn.commit()


def _seed_sources(client: Any, entries: list[dict[str, Any]]) -> None:
    from acp.scheduler.structure_source_store import StructureSourceStore

    manager = client.app.state.job_manager
    store = StructureSourceStore(manager.store.db_path)
    assert store.upsert_index_entries(entries) == len(entries)


def _entry(
    job_id: str,
    project_id: str,
    *,
    checksum: str,
    name: str = "ethanol",
    path: str = "RESULT/input.xyz",
    formula: str = "",
    identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "path": path,
        "project_id": project_id,
        "molecule_name": name,
        "workflow": "Confsearch",
        "formula": formula,
        "atom_count": 9 if formula else None,
        "charge": 0 if formula else None,
        "content_checksum": checksum,
        "molecule_identity": identity,
    }


def test_route_network_merges_by_checksum_and_links_lineage(client: Any) -> None:
    project_id = _default_project_id(client)
    first, second = _batch_create(client, project_id, ["ethanol", "ethanol"])
    _wait_for_terminal(client, [first, second])
    _rewrite_spec_json(client, second, {"source": {"source_job_id": first}})
    _seed_sources(
        client,
        [
            _entry(first, project_id, checksum=_CHECKSUM_A, path="RESULT/a.xyz", formula="C2H6O"),
            _entry(second, project_id, checksum=_CHECKSUM_A, path="RESULT/b.xyz"),
            _entry(second, project_id, checksum="", path="RESULT/unhashed.xyz"),
        ],
    )

    response = _get(client, _NETWORK.format(project_id=project_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["project_id"] == project_id
    assert body["identity_kind"] == "structure_content_v1"
    assert body["truncated"] is False
    assert [node["node_id"] for node in body["nodes"]] == [_CHECKSUM_A]
    node = body["nodes"][0]
    assert node["task_ids"] == sorted([first, second])
    assert node["formula"] == "C2H6O"
    assert node["charge"] == 0
    assert node["atom_count"] == 9
    assert body["edges"] == [
        {"relation": "lineage", "from_task": first, "to_task": second, "depth": 1}
    ]
    assert {"reason": "missing_structure_hash", "task_id": second} in body["skipped"]


def test_route_pages_with_next_cursor(client: Any) -> None:
    project_id = _default_project_id(client)
    (task_id,) = _batch_create(client, project_id, ["ethanol"])
    _wait_for_terminal(client, [task_id])
    _seed_sources(
        client,
        [
            _entry(task_id, project_id, checksum=_CHECKSUM_A, path="RESULT/a.xyz"),
            _entry(task_id, project_id, checksum=_CHECKSUM_B, path="RESULT/b.xyz"),
        ],
    )

    first_page = _get(client, _NETWORK.format(project_id=project_id) + "?limit=1")

    assert first_page.status_code == 200, first_page.text
    first_nodes = first_page.json()["nodes"]
    assert len(first_nodes) == 1
    next_cursor = first_page.json()["next_cursor"]
    assert next_cursor == "1"

    second_page = _get(
        client, _NETWORK.format(project_id=project_id) + f"?limit=1&cursor={next_cursor}"
    )

    assert second_page.status_code == 200, second_page.text
    second_nodes = second_page.json()["nodes"]
    assert len(second_nodes) == 1
    assert {first_nodes[0]["node_id"], second_nodes[0]["node_id"]} == {_CHECKSUM_A, _CHECKSUM_B}
    assert second_page.json()["next_cursor"] is None


def test_route_unknown_project_is_404(client: Any) -> None:
    response = _get(client, _NETWORK.format(project_id="ghost-project"))

    assert response.status_code == 404, response.text
    assert response.json()["detail"] == "Project not found: ghost-project"


def test_route_foreign_project_is_the_same_404(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from acp.api import v2_routes
    from acp_services.workspace.server_identity import ServerIdentity

    project_id = _default_project_id(client)
    # The shipping credential gate reads request.state.request_identity, which only
    # the multi-user identity middleware populates; local-single has no credential,
    # so the same value that middleware would resolve is supplied here.
    client.app.state.server_identity = ServerIdentity.build(verified_project="other")
    monkeypatch.setattr(v2_routes, "_verified_project", lambda request: "other")

    foreign = _get(client, _NETWORK.format(project_id=project_id))
    unknown = _get(client, _NETWORK.format(project_id="ghost-project"))

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.json()["detail"] == f"Project not found: {project_id}"
    assert unknown.json()["detail"] == "Project not found: ghost-project"


@pytest.mark.parametrize("query", ["?limit=0", "?limit=501"])
def test_route_limit_out_of_range_is_422(client: Any, query: str) -> None:
    project_id = _default_project_id(client)

    response = _get(client, _NETWORK.format(project_id=project_id) + query)

    assert response.status_code == 422, response.text


def test_route_garbage_cursor_is_400(client: Any) -> None:
    project_id = _default_project_id(client)

    response = _get(client, _NETWORK.format(project_id=project_id) + "?cursor=garbage")

    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "invalid_cursor"


def _break_identity_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the registry read raise, exactly as a missing/broken registry would."""
    import acp_services.store.molecule_identity as molecule_identity

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise sqlite3.OperationalError("identity registry unavailable")

    monkeypatch.setattr(molecule_identity, "list_project_identities", _boom)


def test_route_network_reflects_identity_registry(client: Any) -> None:
    project_id = _default_project_id(client)
    first, second = _batch_create(client, project_id, ["ethanol", "ethanol"])
    _wait_for_terminal(client, [first, second])
    identity = _identity_row(_MOL_KEY_1)
    _seed_sources(
        client,
        [
            _entry(
                first,
                project_id,
                checksum=_CHECKSUM_A,
                path="RESULT/a.xyz",
                formula="C2H6O",
                identity=identity,
            ),
            _entry(
                second,
                project_id,
                checksum=_CHECKSUM_B,
                path="RESULT/b.xyz",
                identity=identity,
            ),
        ],
    )

    response = _get(client, _NETWORK.format(project_id=project_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert body["identified_nodes"] == 1
    assert body["unidentified_nodes"] == 0
    assert [node["node_id"] for node in body["nodes"]] == [_MOL_KEY_1]
    node = body["nodes"][0]
    assert node["identity_kind"] == MOLECULE_IDENTITY_KIND
    assert node["scheme"] == _SCHEME
    assert node["canonical_smiles"] == "CCO"
    assert node["inchikey"] == "LFQSCWFLJHTTHZ-UHFFFAOYSA-N"
    assert node["charge"] == 0
    assert node["multiplicity"] == 1
    assert node["task_ids"] == sorted([first, second])


def test_route_network_registry_failure_degrades_to_structure_content(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _default_project_id(client)
    (task_id,) = _batch_create(client, project_id, ["ethanol"])
    _wait_for_terminal(client, [task_id])
    _seed_sources(client, [_entry(task_id, project_id, checksum=_CHECKSUM_A, formula="C2H6O")])
    _break_identity_registry(monkeypatch)

    # _get also asserts manager.store.counts() is unchanged (§5).
    response = _get(client, _NETWORK.format(project_id=project_id))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["identity_kind"] == NODE_IDENTITY_KIND
    assert body["identified_nodes"] == 0
    assert body["unidentified_nodes"] == 1
    assert [node["node_id"] for node in body["nodes"]] == [_CHECKSUM_A]


def test_route_network_registry_failure_keeps_identified_node(
    client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = _default_project_id(client)
    (task_id,) = _batch_create(client, project_id, ["ethanol"])
    _wait_for_terminal(client, [task_id])
    _seed_sources(
        client,
        [
            _entry(
                task_id,
                project_id,
                checksum=_CHECKSUM_A,
                formula="C2H6O",
                identity=_identity_row(_MOL_KEY_1),
            )
        ],
    )
    _break_identity_registry(monkeypatch)

    response = _get(client, _NETWORK.format(project_id=project_id))

    # The index key still identifies the node; only its registry metadata is
    # unavailable, which is reported instead of invented.
    assert response.status_code == 200, response.text
    body = response.json()
    assert [node["node_id"] for node in body["nodes"]] == [_MOL_KEY_1]
    assert body["nodes"][0]["canonical_smiles"] == ""
    assert {"reason": SKIP_IDENTITY_METADATA_MISSING, "task_id": task_id} in body["skipped"]
