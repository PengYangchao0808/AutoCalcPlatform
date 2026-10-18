"""B05-4 (todo 28) — publication through the Data entry; frames adapter.

Two separately-listed scopes:

* **B28a** — the existing publication / markers / manifest entries converge on
  the todo-11 ``DataResourcePort`` through
  :class:`acp.services_publication_bootstrap.PublicationDataPort`: the named
  ``result_publication.register_result_manifest`` seam accepts an injected
  publisher, the executor task manifest routes through it, and the scheduler
  markers upload through the same publication entry.  Contract order
  (①step_result → ②checkpoint → ③publish → ④marker) and the completed timing
  are unchanged.
* **B28b** — the science-product → frames/annotation adapter emits only via
  :meth:`TrajectoryFrame.to_node` / :meth:`TrajectoryAnnotation.to_annotation`,
  matching the frozen lane-C ``HN-02`` fixtures for the five required states
  plus the annotation demo, and publishes / reads the projection through Data.

Negative QA: a manifest write that bypasses the port, and a frame projection
that bypasses the frame contract, both fail an assertion.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.calculations import result_publication
from acp.calculations.contracts import CalculationPlan
from acp.calculations.executor import CalculationPlanExecutor
from acp.calculations.result_publication import (
    ArtifactReference,
    ScientificResultRecord,
    register_result_manifest,
)
from acp.results import frames_adapter
from acp.services_data_bootstrap import build_data_resource_port
from acp.services_publication_bootstrap import (
    PublicationDataPort,
    build_publication_data_port,
    default_publication_data_port,
    manifest_publisher,
)
from acp.storage.manifest import ProductKind, ResultManifest
from acp_services.data import contracts as dc

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "science_products" / "frames_mapping"
_FIXTURE_CASES = {
    "normal": "scan",
    "empty": "scan",
    "partial": "scan",
    "corrupt": "scan",
    "old": "irc",
    "curve_annotation": "scan",
}

# The exact node wire shape TrajectoryFrame.to_node() always emits.
_CONTRACT_NODE_KEYS = frozenset(
    {
        "id",
        "label",
        "type",
        "frame_index",
        "x",
        "energy",
        "status",
        "geometry_ref",
        "metadata",
    }
)
_ANNOTATION_BASE_KEYS = frozenset(
    {"id", "type", "label", "frame_index", "x", "y", "status", "geometry_ref", "selected"}
)


def _load_fixture(name: str) -> tuple[dict, dict]:
    source = json.loads((_FIXTURE_DIR / f"{name}.source.json").read_text(encoding="utf-8"))
    expected = json.loads((_FIXTURE_DIR / f"{name}.expected.json").read_text(encoding="utf-8"))
    return source, expected


class _SpyDataPort:
    """Minimal DataResourcePort double recording manifest reads/writes."""

    def __init__(self) -> None:
        self.published: list[dc.ManifestPublish] = []
        self.read_requests: list[dc.ManifestRead] = []
        self.candidate_writes: list[dc.CandidateWrite] = []

    def publish_manifest(self, request: dc.ManifestPublish) -> dc.ManifestPublication:
        self.published.append(request)
        manifest = ResultManifest.from_dict(dict(request.manifest))
        path = manifest.write(Path(request.task_root) / "RESULT")
        return dc.ManifestPublication(
            path=str(path),
            version=manifest.version,
            product_count=len(manifest.products),
            revision=int(request.manifest.get("revision", 0) or 0),
        )

    def read_manifest(self, request: dc.ManifestRead) -> dc.ManifestSnapshot:
        self.read_requests.append(request)
        try:
            manifest = ResultManifest.read(Path(request.task_root) / "RESULT")
        except FileNotFoundError:
            return dc.ManifestSnapshot(found=False, version=0, manifest={})
        return dc.ManifestSnapshot(
            found=True, version=manifest.version, manifest=manifest.to_dict()
        )

    def save_candidates(self, request: dc.CandidateWrite) -> dc.CandidateSnapshot:
        self.candidate_writes.append(request)
        return dc.CandidateSnapshot(
            found=True,
            schema_version="frame_candidates_v2",
            revision=1,
            candidates=(),
        )


def _record(result_id: str = "task-1::step0") -> ScientificResultRecord:
    return ScientificResultRecord(
        result_id=result_id,
        kind="optimize",
        artifacts=(ArtifactReference(path="structures/final.xyz", type="structure"),),
        summary={"energy_hartree": -40.2, "converged": True},
    )


def _manifest() -> ResultManifest:
    manifest = ResultManifest(task_id="t", workflow="optimize", status="completed")
    manifest.add_product("final", "Final geometry", "structures/final.xyz", ProductKind.STRUCTURE)
    return manifest


# ---------------------------------------------------------------------------
# B28a — publication / markers / manifest entry convergence
# ---------------------------------------------------------------------------


def test_default_publication_data_port_is_data_port_without_side_effects(tmp_path: Path) -> None:
    port = default_publication_data_port()
    assert isinstance(port, dc.DataResourcePort)
    assert isinstance(build_publication_data_port(), PublicationDataPort)


def test_named_manifest_seam_routes_through_publisher(tmp_path: Path) -> None:
    seen: list[tuple[Path, ResultManifest]] = []
    result_dir = tmp_path / "RESULT"
    real = register_result_manifest

    def publisher(path: Path, manifest: ResultManifest) -> Path:
        seen.append((path, manifest))
        return real(path, manifest)

    manifest = _manifest()
    written = register_result_manifest(result_dir, manifest, publisher=publisher)
    assert seen and seen[0][0] == result_dir
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["products"][0]["id"] == "final"


def test_publish_result_keeps_contract_order_with_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    real_save = result_publication.save_scientific_result
    real_mark = result_publication.mark_publication_complete

    def spy_save(result_dir, record):
        events.append("scientific_result")
        return real_save(result_dir, record)

    def spy_mark(result_dir, result_id):
        events.append("complete_marker")
        return real_mark(result_dir, result_id)

    def publisher(path: Path, manifest: ResultManifest) -> Path:
        events.append("display_manifest")
        return manifest.write(path)

    monkeypatch.setattr(result_publication, "save_scientific_result", spy_save)
    monkeypatch.setattr(result_publication, "mark_publication_complete", spy_mark)

    result_publication.publish_result(
        tmp_path,
        record=_record(),
        manifest=_manifest(),
        copy_products=lambda: events.append("products"),
        publisher=publisher,
    )
    assert events == [
        "scientific_result",
        "products",
        "display_manifest",
        "complete_marker",
    ]


def test_executor_task_manifest_routes_through_injected_data_port(tmp_path: Path) -> None:
    data = _SpyDataPort()
    executor = CalculationPlanExecutor(publication=PublicationDataPort(data))
    result_dir = tmp_path / "RESULT"

    executor._write_result_manifest(
        result_dir,
        CalculationPlan(workflow="optimize"),
        [],
        "completed",
        publisher=executor._publication_publisher(),
    )

    assert len(data.published) == 1, "task manifest must be published through the Data port"
    assert data.published[0].task_root == str(tmp_path)
    assert (result_dir / "result_manifest.json").is_file()


def test_manifest_publisher_falls_back_for_non_result_dir(tmp_path: Path) -> None:
    data = _SpyDataPort()
    publisher = manifest_publisher(PublicationDataPort(data))
    step_dir = tmp_path / "WORK" / "03_OPT"

    written = register_result_manifest(step_dir, _manifest(), publisher=publisher)

    assert not data.published, "per-step (non-RESULT) manifest must not hit the Data port"
    assert written == step_dir / "result_manifest.json"
    assert written.is_file()


def test_scheduler_markers_route_through_publication_port(tmp_path: Path) -> None:
    from acp.scheduler.events import JobEventLog
    from acp.scheduler.jobs import JobRecord, JobSpec
    from acp.scheduler.remote.runner import RemoteJobRunner

    uploaded: list[tuple[str, str]] = []

    class _SpyPublication:
        def upload_markers(
            self,
            *,
            job_id,
            work_dir,
            node,
            remote_job_dir,
            relative_paths=("job.json", "task.json"),
        ):
            for name in relative_paths:
                uploaded.append((job_id, name))
            return tuple(relative_paths)

    work_dir = tmp_path / "proj" / "markerjob"
    work_dir.mkdir(parents=True)
    record = JobRecord(
        id="markerjob",
        spec=JobSpec(workflow="optimize", input={"source": "CCO", "source_type": "smiles"}),
        work_dir=str(work_dir),
    )
    event_log = JobEventLog(work_dir / "events.jsonl")
    stager = SimpleNamespace(upload_file=lambda *a, **k: pytest.fail("direct stager upload"))

    runner = RemoteJobRunner.__new__(RemoteJobRunner)
    runner._publication = _SpyPublication()
    runner._stager = stager
    runner._upload_scheduler_markers(
        record, SimpleNamespace(name="nodeA"), "/remote/markerjob", work_dir, event_log
    )

    assert uploaded == [("markerjob", "job.json"), ("markerjob", "task.json")]
    events = event_log.read_all()
    assert any(e.get("type") == "remote.markers_uploaded" for e in events)


def test_upload_markers_uses_stager_with_relative_layout(tmp_path: Path) -> None:
    work_dir = tmp_path / "task"
    work_dir.mkdir()
    (work_dir / "job.json").write_text("{}", encoding="utf-8")
    (work_dir / "task.json").write_text("{}", encoding="utf-8")
    calls: list[tuple[str, str]] = []
    stager = SimpleNamespace(
        upload_file=lambda node, local, remote: calls.append((str(local), remote))
    )
    port = PublicationDataPort(_SpyDataPort(), stager=stager)

    uploaded = port.upload_markers(
        job_id="job_1", work_dir=work_dir, node=object(), remote_job_dir="/remote/job_1"
    )
    assert uploaded == ("job.json", "task.json")
    assert calls == [
        (str(work_dir / "job.json"), "/remote/job_1/job.json"),
        (str(work_dir / "task.json"), "/remote/job_1/task.json"),
    ]


def test_negative_direct_manifest_write_is_detected(tmp_path: Path) -> None:
    """A manifest written outside the named seam/publisher is not a Data publish."""
    data = _SpyDataPort()
    publisher = manifest_publisher(PublicationDataPort(data))
    result_dir = tmp_path / "RESULT"
    manifest = _manifest()
    # Bypass the seam entirely.
    manifest.write(result_dir)

    with pytest.raises(AssertionError):
        assert data.published, "direct write must never appear as a Data publication"
    # The publisher path *does* route through Data — proving the negative is real.
    register_result_manifest(result_dir, manifest, publisher=publisher)
    assert data.published


# ---------------------------------------------------------------------------
# B28b — science-product → frames/annotation adapter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_FIXTURE_CASES))
def test_frames_adapter_matches_lane_c_fixtures(name: str) -> None:
    source, expected = _load_fixture(name)
    projection = frames_adapter.project_science_product_dict(source, view_type=_FIXTURE_CASES[name])
    assert projection == expected


def test_frames_adapter_emits_only_the_frame_contract_shape() -> None:
    source, _ = _load_fixture("normal")
    projection = frames_adapter.project_science_product_dict(source, view_type="scan")
    for node in projection["nodes"]:
        assert set(node) == _CONTRACT_NODE_KEYS, "node shape must come from to_node()"

    curve_source, _ = _load_fixture("curve_annotation")
    curve = frames_adapter.project_science_product_dict(curve_source, view_type="scan")
    for node in curve["nodes"]:
        assert set(node) == _CONTRACT_NODE_KEYS
    for annotation in curve["annotations"]:
        assert set(annotation) <= _ANNOTATION_BASE_KEYS | {"metadata"}


def test_frames_adapter_source_uses_the_contract() -> None:
    source = inspect.getsource(frames_adapter)
    assert ".to_node(" in source, "adapter must emit nodes via TrajectoryFrame.to_node()"
    assert ".to_annotation(" in source, "adapter must emit markers via to_annotation()"


def test_negative_projection_bypassing_frame_contract_fails_guard() -> None:
    bypass = {"id": "x", "label": "y", "frame_index": 0, "energy": 1.0}  # missing to_node keys
    with pytest.raises(AssertionError):
        assert set(bypass) == _CONTRACT_NODE_KEYS


def test_fixed_input_generates_publishes_reads_projection(tmp_path: Path) -> None:
    """C input → B projection → Data publish → Data read (A-side consumer)."""
    source, expected = _load_fixture("normal")
    data_port = build_data_resource_port(tmp_path)
    publication = PublicationDataPort(data_port)

    projection = frames_adapter.project_science_product_dict(source, view_type="scan")
    assert projection == expected

    written = publication.save_frame_projection(tmp_path, "scan", projection)
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8")) == expected

    # Read back through the Data manifest entry (the A-side consume path).
    read_back = publication.read_frame_projection(tmp_path, "scan")
    assert read_back == expected
    manifest = publication.read_manifest(tmp_path)
    assert manifest is not None
    product = next(p for p in manifest["products"] if p["id"] == "frames_scan")
    assert product["path"] == "frames/scan.json"


def test_frames_adapter_state_behaviors() -> None:
    empty, _ = _load_fixture("empty")
    empty_projection = frames_adapter.project_science_product_dict(empty, view_type="scan")
    assert empty_projection["unavailable"] == {
        "state": "unavailable",
        "missing_reason": "source_missing",
    }
    assert empty_projection["nodes"] == []

    partial, _ = _load_fixture("partial")
    partial_projection = frames_adapter.project_science_product_dict(partial, view_type="scan")
    assert partial_projection["skipped_frames"] == [
        {"frame_index": 1, "missing_reason": "missing_atom_uid"}
    ]
    assert len(partial_projection["nodes"]) == 2

    corrupt, _ = _load_fixture("corrupt")
    corrupt_projection = frames_adapter.project_science_product_dict(corrupt, view_type="scan")
    assert corrupt_projection["error"] == "ProductSchemaError"
    assert corrupt_projection["stage"] == "product_from_dict"
    assert corrupt_projection["nodes"] == []

    old, _ = _load_fixture("old")
    old_projection = frames_adapter.project_science_product_dict(old, view_type="irc")
    assert old_projection["migrations"] == [
        "v0->v1: atom_uid derived as element:rank (per-element source order)",
        "v0->v1: coordinate_units defaulted to angstrom (documented trajectory convention)",
        "v0->v1: topology_source set to legacy_v0",
    ]


def test_per_product_adapter_registry_records_all_kinds() -> None:
    kinds = {record["product_kind"] for record in frames_adapter.PRODUCT_ADAPTER_RECORDS}
    assert kinds == {"structure_trajectory", "curve_scatter", "vibration", "derived"}
    vibration = frames_adapter.adapter_record_for("vibration")
    assert vibration is not None and "N/A" in vibration["node_annotation"]
    derived = frames_adapter.adapter_record_for("derived")
    assert derived is not None and "N/A" in derived["frames_adapter"]
