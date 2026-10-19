"""B08-5 (todo 46) — read-only research evidence projection acceptance tests.

Covers the todo's acceptance clauses:

* the five frozen edge types (uses / produces / derives / revision / reuses);
* versioned revisions, append-only, with the original scientific result never
  overwritten;
* atom / frame / mode identity-mapping preservation (native indices never
  renumbered) and verbatim carry of the C02 derived fields;
* an honest C02 field-map status (cross-lane e2e stays NOT_VERIFIED);
* the additive read-only endpoint shape and its read-only behaviour.
"""

from __future__ import annotations

import dataclasses
import json
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp_services.runtime.lineage import (
    C02_FIELD_MAP,
    C02_FIELD_MAP_STATUS,
    EVIDENCE_PROJECTION_VERSION,
    EdgeType,
    IdentityMapping,
    LineageEdge,
    MappingKind,
    OriginalResultOverwriteError,
    RevisionLedger,
    TaskEvidenceBuilder,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIELD_MAP_DOC = _REPO_ROOT / "tests" / "baseline" / "services-evidence" / "lineage_field_map.md"


def _c02_derived_product() -> dict[str, Any]:
    return {
        "product_kind": "derived",
        "state": "available",
        "complete": True,
        "identity": {
            "product_kind": "derived",
            "generator": "cccp.calculation.products.derived.build_derived_product",
            "algorithm": "boltzmann",
            "algorithm_version": "1",
            "input_digest": "sha256:feedface",
        },
        "payload": {
            "sources": [
                {"source_kind": "final", "artifact": "RESULT/conf/a.xyz", "native_index": 3},
                {"source_kind": "orca_output", "artifact": "RESULT/conf/a.out", "native_index": 7},
            ],
            "source_versions": ["v1", "v2"],
            "parameters": [
                {"name": "temperature", "value": 298.15, "unit": "K"},
            ],
            "raw_io": [{"name": "weights", "role": "output", "value": [0.6, 0.4]}],
            "reference_frame": {"axes": "cartesian", "energy_zero": "electronic_ground_state"},
        },
    }


# ── ① five edge types ─────────────────────────────────────────────────


def test_builder_records_all_five_edge_types() -> None:
    builder = TaskEvidenceBuilder("task-1")
    builder.add_node("task-1", kind="task", label="opt")
    builder.add_use("task-1", "upstream-1", relation="input.source_job_id")
    builder.add_produce("task-1", "RESULT/structures/opt.xyz", role="structure")
    builder.add_c02_derived("task-1", _c02_derived_product())
    builder.ledger.register_original("task-1", {"energy": -1.0})
    builder.add_revision("task-1", revision_kind="selection", payload={"selected": "frame:2"})
    builder.add_reuse("task-1", "task-1#attempt1", attempt=1)

    projection = builder.build()

    assert {edge.edge_type for edge in projection.edges} == set(EdgeType)
    for edge_type in EdgeType:
        assert len(projection.edges_of_type(edge_type)) == 1
    assert len(projection.edges_from("task-1")) == 5


def test_edge_dict_round_trip() -> None:
    edge = LineageEdge(
        edge_type=EdgeType.DERIVES,
        subject="t",
        object_ref="sha256:x",
        role="derived",
        revision=2,
        mappings=(IdentityMapping(native_index=5, kind=MappingKind.FRAME, stable_id="frame:5"),),
        evidence={"state": "available"},
    )
    restored = LineageEdge.from_dict(edge.to_dict())
    assert restored == edge


# ── ② versioned revisions ─────────────────────────────────────────────


def test_revisions_are_versioned_and_append_only() -> None:
    ledger = RevisionLedger()
    ledger.register_original("t", {"energy": -1.0})

    first = ledger.record_revision("t", revision_kind="selection", payload={"pick": "a"})
    second = ledger.record_revision("t", revision_kind="revision", payload={"pick": "b"})
    third = ledger.record_revision("t", revision_kind="revision", payload={"pick": "c"})

    assert (first.revision, second.revision, third.revision) == (1, 2, 3)
    assert (first.supersedes, second.supersedes, third.supersedes) == (0, 1, 2)
    assert [r.revision for r in ledger.versions("t")] == [1, 2, 3]
    assert ledger.latest("t") is third
    assert [r.revision for r in ledger.versions("t")] == [1, 2, 3]


def test_revision_edges_carry_revision_number() -> None:
    builder = TaskEvidenceBuilder("t")
    builder.ledger.register_original("t", {"v": 1})
    builder.add_revision("t", revision_kind="selection", payload={"pick": 1})
    builder.add_revision("t", revision_kind="revision", payload={"pick": 2})

    revs = builder.build().edges_of_type(EdgeType.REVISION)
    assert [e.revision for e in revs] == [1, 2]
    assert all(e.subject == "t" for e in revs)


# ── ③ original result never overwritten ───────────────────────────────


def test_original_result_is_never_overwritten() -> None:
    ledger = RevisionLedger()
    original = {"energy": -1.0, "frames": [1, 2, 3]}
    digest = ledger.register_original("t", original)

    ledger.record_revision("t", revision_kind="selection", payload={"selected": "frame:1"})
    ledger.record_revision("t", revision_kind="revision", payload={"selected": "frame:2"})

    assert ledger.original("t") == original
    assert ledger.original_digest("t") == digest
    assert digest.startswith("sha256:")
    assert ledger.latest("t").original_digest == digest  # type: ignore[union-attr]

    # The original is write-once: a second registration is rejected, not applied.
    with pytest.raises(OriginalResultOverwriteError):
        ledger.register_original("t", {"energy": 999.0})
    assert ledger.original("t") == original

    # A human revision may not claim to replace the authoritative result.
    with pytest.raises(OriginalResultOverwriteError):
        ledger.record_revision(
            "t", revision_kind="revision", payload={"overwrite_original": True, "energy": 0.0}
        )
    assert ledger.original("t") == original


def test_original_is_returned_as_isolated_copy() -> None:
    ledger = RevisionLedger()
    ledger.register_original("t", {"energy": -1.0})
    copy = ledger.original("t")
    assert copy is not None
    copy["energy"] = 42.0
    assert ledger.original("t") == {"energy": -1.0}


# ── ④ mapping preservation + verbatim C02 fields ──────────────────────


def test_c02_derived_fields_are_verbatim_and_native_index_preserved() -> None:
    product = _c02_derived_product()
    builder = TaskEvidenceBuilder("t")
    edge = builder.add_c02_derived("t", product)

    assert edge.edge_type is EdgeType.DERIVES
    assert edge.object_ref == "sha256:feedface"
    assert edge.evidence["identity"] == product["identity"]
    assert edge.evidence["sources"] == product["payload"]["sources"]
    assert edge.evidence["source_versions"] == ["v1", "v2"]
    assert edge.evidence["parameters"] == product["payload"]["parameters"]
    assert edge.evidence["raw_io"] == product["payload"]["raw_io"]
    assert edge.evidence["reference_frame"] == product["payload"]["reference_frame"]

    # Native indices are preserved and the opaque source_kind is not classified.
    assert [(m.native_index, m.source_kind, m.kind) for m in edge.mappings] == [
        (3, "final", None),
        (7, "orca_output", None),
    ]


def test_identity_mapping_preserves_atom_frame_mode() -> None:
    mappings = (
        IdentityMapping(native_index=0, kind=MappingKind.ATOM, stable_id="C:0"),
        IdentityMapping(
            native_index=4, kind=MappingKind.FRAME, stable_id="structure_trajectory:frame:4"
        ),
        IdentityMapping(native_index=11, kind=MappingKind.MODE, stable_id="11"),
    )
    builder = TaskEvidenceBuilder("t")
    edge = builder.add_produce("t", "RESULT/x", mappings=mappings)

    restored = LineageEdge.from_dict(edge.to_dict())
    assert restored.mappings == mappings
    assert [m.native_index for m in restored.mappings] == [0, 4, 11]


# ── ⑤ read-only projection semantics ──────────────────────────────────


def test_projection_is_frozen_and_chain_is_read_only() -> None:
    builder = TaskEvidenceBuilder("a")
    builder.add_use("a", "b")
    builder.add_use("b", "c")
    projection = builder.build()

    assert dataclasses.is_dataclass(projection)
    assert projection.__dataclass_params__.frozen
    assert [e.subject for e in projection.chain("a")] == ["a", "b"]
    assert projection.chain("a", max_depth=1) == projection.chain("a")[:1]
    assert set(projection.to_dict()) == {
        "task_id",
        "version",
        "nodes",
        "edges",
        "revisions",
        "field_map",
        "field_map_status",
    }
    assert projection.to_dict()["version"] == EVIDENCE_PROJECTION_VERSION


# ── ⑥ C02 field map honesty ───────────────────────────────────────────


def test_c02_field_map_is_verbatim_and_honest() -> None:
    assert C02_FIELD_MAP, "field map must not be empty"
    assert all(entry.verbatim for entry in C02_FIELD_MAP)
    assert C02_FIELD_MAP_STATUS["cross_lane_e2e"] == "NOT_VERIFIED"
    assert C02_FIELD_MAP_STATUS["provider_status"] == "delivered"
    assert "HN-02" in C02_FIELD_MAP_STATUS["channel"]
    assert _FIELD_MAP_DOC.is_file(), "tracked field-map doc missing"
    doc = _FIELD_MAP_DOC.read_text(encoding="utf-8")
    assert "HN-02" in doc
    assert "NOT_VERIFIED" in doc


def test_lineage_module_has_no_acp_import() -> None:
    from tests.test_acp_services_isolation import scan_for_acp_imports

    source = (
        _REPO_ROOT / "src" / "acp_services" / "runtime" / "lineage.py"
    ).read_text(encoding="utf-8")
    assert scan_for_acp_imports(source) == []


# ── ⑦ additive read-only endpoint ─────────────────────────────────────


def _make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def client(tmp_path: Path) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path) as c:
        c.app.state.job_manager._execute_submission = lambda job_id: None  # type: ignore[method-assign]
        yield c


def _seed_job(
    client: TestClient,
    *,
    job_id: str,
    work_dir: Path,
    spec_input: dict[str, Any],
    manifest_products: list[dict[str, Any]] | None = None,
) -> None:
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus

    manager = client.app.state.job_manager
    work_dir.mkdir(parents=True, exist_ok=True)
    if manifest_products is not None:
        result_dir = work_dir / "RESULT"
        result_dir.mkdir(parents=True, exist_ok=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "task_id": job_id,
                    "workflow": "Confsearch",
                    "status": "completed",
                    "products": manifest_products,
                }
            ),
            encoding="utf-8",
        )
    spec = JobSpec(
        workflow="Confsearch",
        name=job_id,
        molecule_name="CCO",
        task_name="opt",
        remark="r",
        project_id="",
        input=spec_input,
    )
    record = JobRecord(
        id=job_id,
        spec=spec,
        status=JobStatus.COMPLETED,
        work_dir=str(work_dir),
        project_id="",
        attempt=1,
        revision=0,
    )
    manager.store.create(record)
    manager.tasks.sync_from_job(record)


def _db_snapshot(client: TestClient) -> tuple[list[Any], list[Any]]:
    import sqlite3

    db_path = client.app.state.job_manager.store.db_path
    with sqlite3.connect(str(db_path)) as conn:
        jobs = conn.execute("SELECT * FROM jobs").fetchall()
        tasks = conn.execute("SELECT * FROM tasks").fetchall()
    return jobs, tasks


def test_endpoint_projection_shape(client: TestClient, tmp_path: Path) -> None:
    job_id = "20260101_001_Confsearch"
    _seed_job(
        client,
        job_id=job_id,
        work_dir=tmp_path / "work" / job_id,
        spec_input={"source": {"source_job_id": "upstream-1"}},
        manifest_products=[
            {"id": "opt", "label": "Optimized", "path": "structures/opt.xyz", "kind": "structure"}
        ],
    )

    resp = client.get(f"/api/v2/tasks/{job_id}/lineage/projection")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "task_id",
        "version",
        "nodes",
        "edges",
        "revisions",
        "field_map",
        "field_map_status",
    }
    assert body["task_id"] == job_id
    assert body["field_map_status"]["cross_lane_e2e"] == "NOT_VERIFIED"

    uses = [e for e in body["edges"] if e["edge_type"] == "uses"]
    assert uses and uses[0]["object_ref"] == "upstream-1"
    assert "source_job_id" in uses[0]["evidence"]["relation"]

    produces = [e for e in body["edges"] if e["edge_type"] == "produces"]
    assert produces and produces[0]["role"] == "structure"
    assert produces[0]["object_ref"] == "structures/opt.xyz"

    assert any(node["node_id"] == job_id and node["kind"] == "task" for node in body["nodes"])


def test_endpoint_unknown_task_404(client: TestClient) -> None:
    resp = client.get("/api/v2/tasks/does-not-exist/lineage/projection")
    assert resp.status_code == 404


def test_endpoint_is_read_only(client: TestClient, tmp_path: Path) -> None:
    job_id = "20260101_002_Confsearch"
    _seed_job(
        client,
        job_id=job_id,
        work_dir=tmp_path / "work2" / job_id,
        spec_input={"source": {"source_job_id": "upstream-2"}},
    )

    before = _db_snapshot(client)
    resp = client.get(f"/api/v2/tasks/{job_id}/lineage/projection")
    assert resp.status_code == 200
    assert _db_snapshot(client) == before
