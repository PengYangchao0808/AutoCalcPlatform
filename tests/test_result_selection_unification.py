"""Behavioral regression tests for stage-based reusable structure policy."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from acp.results.structure_policy import single_geometry, reusable_product, WORKFLOW_SOURCE_ROLES
from acp.results.structure_snapshots import preserve_outputs
from acp.storage.manifest import ResultManifest
from acp.scheduler.job_edit import resolve_previous_outputs
from acp.scheduler.structure_source_store import StructureSourceStore, source_uid_for

XYZ = "2\nTAG: TS\nH 0 0 0\nH 0 0 0.7\n"

@pytest.mark.parametrize("text", [XYZ + XYZ, "2\ninvalid\nH nan 0 0\nH 0 0 1\n", "2\nshort\nH 0 0 0\n", "1\nbad element\nQq 0 0 0\n"])
def test_single_geometry_rejects_unsafe_frames(text):
    assert single_geometry(text) is None


def test_outputs_keep_both_irc_directions_and_exclude_path(tmp_path):
    result = tmp_path / "RESULT"
    result.mkdir()
    manifest = ResultManifest(workflow="irc", status="failed")
    for direction in ("forward", "reverse"):
        (result / (direction + ".xyz")).write_text(XYZ, encoding="utf-8")
        manifest.add_product("irc_" + direction + "_endpoint", direction, direction + ".xyz", "irc_endpoint")
    (result / "path.xyz").write_text(XYZ + XYZ, encoding="utf-8")
    manifest.add_product("irc_reverse_path", "path", "path.xyz", "structure")
    manifest.write(result)
    outputs = resolve_previous_outputs(tmp_path, job_id="j", attempt=2)
    assert [o["direction"] for o in outputs] == ["forward", "reverse"]
    assert outputs[0]["source_uid"] != outputs[1]["source_uid"]
    assert outputs[0]["content_checksum"] == outputs[1]["content_checksum"]


def test_snapshots_survive_output_removal_and_are_idempotent(tmp_path):
    task = tmp_path / "task"
    result = task / "RESULT"
    result.mkdir(parents=True)
    (result / "opt.xyz").write_text(XYZ, encoding="utf-8")
    manifest = ResultManifest(workflow="BatchOptimize")
    manifest.add_product("batch_a", "A", "opt.xyz", "structure", {"optimization_status":"converged"})
    manifest.write(result)
    outputs = resolve_previous_outputs(task, job_id="j", attempt=1)
    refs = preserve_outputs(tmp_path, task, outputs, job_id="j", attempt=1, input_spec={"source":XYZ})
    preserve_outputs(tmp_path, task, outputs, job_id="j", attempt=1, input_spec={"source":XYZ})
    (result / "opt.xyz").unlink()
    assert (task / refs[0]["path"]).read_text(encoding="utf-8") == XYZ
    assert len(json.loads((task / ".structure_history/sources.json").read_text(encoding="utf-8"))) == 1
    assert len(list((tmp_path / ".structure_snapshots/objects").glob("*.xyz"))) == 1


def test_snapshot_failure_keeps_existing_results(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    original = task / "original.xyz"
    original.write_text(XYZ, encoding="utf-8")
    with pytest.raises(ValueError):
        preserve_outputs(tmp_path, task, [{"xyz_text":XYZ+XYZ}], job_id="j", attempt=0, input_spec={})
    assert original.read_text(encoding="utf-8") == XYZ


def test_refresh_retains_name_tags_and_trash(tmp_path):
    store = StructureSourceStore(tmp_path / "db.sqlite")
    row = {"job_id":"j", "path":"RESULT/opt.xyz", "role":"TS", "has_3d":True}
    store.upsert_index_entries([row])
    uid = source_uid_for("j", "RESULT/opt.xyz")
    store.set_custom_name(uid, "User name", 0)
    store.add_tags(uid, ["publication"], 1)
    store.set_candidate_status(uid, "trash", expected_revision=0)
    store.upsert_index_entries([row], replace_job_id="j", discovery_version=2)
    item = store.get(uid)
    assert item["custom_name"] == "User name"
    assert item["tags"] == ["publication"]
    assert item["usage_status"] == "trash"


def test_context_sort_happens_before_pagination(tmp_path):
    store = StructureSourceStore(tmp_path / "db.sqlite")
    store.upsert_index_entries([
        {"job_id":"j", "path":"int.xyz", "role":"INT", "produced_at":"2026-09-30"},
        {"job_id":"j", "path":"ts.xyz", "role":"TS", "produced_at":"2026-09-01"}])
    page = store.query_sources(all_projects=True, sort="context", context_workflow="irc", limit=1)
    assert page["items"][0]["role"] == "TS"
    with pytest.raises(ValueError, match="fingerprint"):
        store.query_sources(all_projects=True, sort="context", context_workflow="nmr", limit=1, cursor=page["next_cursor"])


def test_irc_iteration_limit_is_not_endpoint_completion():
    from acp.calculations.primitives.irc import _completed_directions
    path = Path(__file__).parent / "fixtures/irc/orca_irc_h2o2.out"
    assert _completed_directions(SimpleNamespace(log_file=path), {"forward":{},"reverse":{}}, True) == set()
    raw = SimpleNamespace(metadata={"direction_status":{"forward":"completed","reverse":"failed"}})
    assert _completed_directions(raw, {"forward":{},"reverse":{}}, False) == {"forward"}


def test_failed_opt_and_automatic_pes_are_not_reusable():
    assert not reusable_product({"id":"opt", "kind":"structure", "metadata":{"optimization_status":"failed"}})
    assert not reusable_product({"id":"ts_guess_001", "kind":"structure"})
    assert reusable_product({"id":"ts_guess_001", "kind":"structure", "metadata":{"selection_source":"manual"}})


def test_all_active_workflows_have_display_policy():
    from acp.catalog import WORKFLOW_CATALOG
    active = {row["id"] for row in WORKFLOW_CATALOG if row.get("status") == "active"}
    assert active <= WORKFLOW_SOURCE_ROLES.keys()


def test_historical_backfill_requires_bound_convergence_and_is_idempotent(tmp_path):
    from acp.results.structure_migration import backfill_successful_optimizations
    step = tmp_path / "WORK" / "item_A" / "optimize"
    step.mkdir(parents=True)
    (step / "final.xyz").write_text(XYZ, encoding="utf-8")
    evidence = step / "optimization_trajectory.json"
    payload = {"item_id":"A", "converged":False, "status":"failed", "cycles":[{"geometry_ref":"final.xyz"}]}
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert backfill_successful_optimizations(tmp_path, "BatchOptimize") == []
    payload.update(converged=True, status="completed")
    evidence.write_text(json.dumps(payload), encoding="utf-8")
    assert len(backfill_successful_optimizations(tmp_path, "BatchOptimize", dry_run=True)) == 1
    assert not (tmp_path / "RESULT").exists()
    assert len(backfill_successful_optimizations(tmp_path, "BatchOptimize")) == 1
    assert backfill_successful_optimizations(tmp_path, "BatchOptimize") == []
    assert len(resolve_previous_outputs(tmp_path, job_id="j")) == 1


def test_strict_snapshot_resolution_blocks_corrupt_manifest(tmp_path):
    result = tmp_path / "RESULT"
    result.mkdir()
    (result / "result_manifest.json").write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="清单"):
        resolve_previous_outputs(tmp_path, strict=True)


# ---------------------------------------------------------------------------
# todo 8 (GAP-7): unified collection policy for recalc with retained audit.
# ---------------------------------------------------------------------------

ENSEMBLE = XYZ + XYZ  # two complete frames = a trajectory, never a single geometry


def test_collection_product_recognizes_both_collection_policies():
    """One policy: historical no-metadata ids + new writer auto_reusable=False."""
    from acp.results.structure_policy import collection_product

    assert collection_product({"id": "all_conformers", "kind": "structure"})
    assert collection_product({"id": "confsearch_final_conformers", "kind": "structure"})
    assert collection_product(
        {"id": "ensemble", "kind": "structure", "metadata": {"auto_reusable": False}}
    )
    # A genuine single-frame product is never a collection.
    assert collection_product({"id": "global_min", "kind": "structure"}) is None
    assert collection_product({"id": "confsearch_conf_001", "kind": "structure"}) is None


def test_reusable_product_excludes_collections():
    """Structure-source indexing and remote prefetch consume ``reusable_product``."""
    assert not reusable_product({"id": "all_conformers", "kind": "structure"})
    assert not reusable_product({"id": "confsearch_final_conformers", "kind": "structure"})
    assert not reusable_product(
        {"id": "ensemble", "kind": "xyz", "metadata": {"auto_reusable": False}}
    )
    assert reusable_product({"id": "rank1", "kind": "structure"})


def test_resolve_previous_outputs_skips_collections_with_audit(tmp_path):
    """Both historical collection ids are skipped (strict) and audited."""
    result = tmp_path / "RESULT"
    result.mkdir()
    (result / "all_conformers.xyz").write_text(ENSEMBLE, encoding="utf-8")
    (result / "final_conformers.xyz").write_text(ENSEMBLE, encoding="utf-8")
    (result / "best.xyz").write_text(XYZ, encoding="utf-8")
    manifest = ResultManifest(workflow="Confsearch")
    manifest.add_product(
        "all_conformers", "Ranked conformers (XYZ)", "all_conformers.xyz", "structure"
    )
    manifest.add_product(
        "confsearch_final_conformers",
        "Refined conformers (XYZ)",
        "final_conformers.xyz",
        "structure",
    )
    manifest.add_product("rank1", "Rank 1", "best.xyz", "structure")
    manifest.write(result)
    skipped: list[dict] = []
    outputs = resolve_previous_outputs(tmp_path, job_id="j", strict=True, skipped=skipped)
    assert [o["entry_id"] for o in outputs] == ["rank1"]
    by_id = {row["id"]: row for row in skipped}
    assert set(by_id) == {"all_conformers", "confsearch_final_conformers"}
    for row in skipped:
        assert row["path"]
        assert row["reason"]


def test_resolve_previous_outputs_records_marked_collection(tmp_path):
    """The new writer semantics (auto_reusable=False) is skipped with audit."""
    result = tmp_path / "RESULT"
    result.mkdir()
    (result / "ensemble.xyz").write_text(ENSEMBLE, encoding="utf-8")
    (result / "best.xyz").write_text(XYZ, encoding="utf-8")
    manifest = ResultManifest(workflow="energy")
    manifest.add_product(
        "ensemble", "Ensemble", "ensemble.xyz", "structure", metadata={"auto_reusable": False}
    )
    manifest.add_product("global_min", "Global minimum", "best.xyz", "structure")
    manifest.write(result)
    skipped: list[dict] = []
    outputs = resolve_previous_outputs(tmp_path, job_id="j", strict=True, skipped=skipped)
    assert [o["entry_id"] for o in outputs] == ["global_min"]
    assert [row["id"] for row in skipped] == ["ensemble"]


def test_resolve_previous_outputs_collection_only_strict_does_not_raise(tmp_path):
    """A collection-only job must not abort a strict destructive rerun."""
    result = tmp_path / "RESULT"
    result.mkdir()
    (result / "all_conformers.xyz").write_text(ENSEMBLE, encoding="utf-8")
    manifest = ResultManifest(workflow="Confsearch")
    manifest.add_product(
        "all_conformers", "Ranked conformers (XYZ)", "all_conformers.xyz", "structure"
    )
    manifest.write(result)
    skipped: list[dict] = []
    outputs = resolve_previous_outputs(tmp_path, job_id="j", strict=True, skipped=skipped)
    assert outputs == []
    assert [row["id"] for row in skipped] == ["all_conformers"]


def test_resolve_previous_outputs_corrupt_single_still_blocks(tmp_path):
    """The collection skip must NOT weaken the corrupt-single-frame guard."""
    result = tmp_path / "RESULT"
    result.mkdir()
    (result / "broken.xyz").write_text("2\nshort\nH 0 0 0\n", encoding="utf-8")
    manifest = ResultManifest(workflow="Confsearch")
    manifest.add_product("rank1", "Rank 1", "broken.xyz", "structure")
    manifest.write(result)
    with pytest.raises(ValueError, match="单帧"):
        resolve_previous_outputs(tmp_path, job_id="j", strict=True)


def test_preserve_outputs_never_snapshots_collections(tmp_path):
    """Even if a collection row reaches preserve_outputs, it is filtered, not raised."""
    task = tmp_path / "task"
    task.mkdir()
    refs = preserve_outputs(
        tmp_path,
        task,
        [
            {
                "entry_id": "all_conformers",
                "product_id": "all_conformers",
                "path": "RESULT/all_conformers.xyz",
                "xyz_text": ENSEMBLE,
            },
            {
                "entry_id": "ensemble",
                "product_id": "ensemble",
                "auto_reusable": False,
                "path": "RESULT/ensemble.xyz",
                "xyz_text": ENSEMBLE,
            },
            {
                "entry_id": "rank1",
                "product_id": "rank1",
                "path": "RESULT/best.xyz",
                "xyz_text": XYZ,
            },
        ],
        job_id="j",
        attempt=1,
        input_spec={},
    )
    assert [ref["entry_id"] for ref in refs] == ["rank1"]


def test_remote_prefetch_uses_collection_policy(tmp_path):
    """Remote reusable-geometry prefetch must use the same collection policy."""
    from acp.results.remote_structure_cache import RemoteStructureCache

    cached = tmp_path / "cached"
    result = cached / "RESULT"
    result.mkdir(parents=True)
    (result / "all_conformers.xyz").write_text(ENSEMBLE, encoding="utf-8")
    (result / "best.xyz").write_text(XYZ, encoding="utf-8")
    manifest = ResultManifest(workflow="Confsearch")
    manifest.add_product(
        "all_conformers", "Ranked conformers (XYZ)", "all_conformers.xyz", "structure"
    )
    manifest.add_product("rank1", "Rank 1", "best.xyz", "structure")
    manifest.write(result)

    cache = RemoteStructureCache(tmp_path / "remote_cache")
    requested: list[str] = []

    def fake_fetch(record, rel_path, raise_errors=False):  # noqa: ANN001, ANN202
        requested.append(rel_path)
        return cached / rel_path

    cache.fetch = fake_fetch  # type: ignore[method-assign]
    cache.fetch_reusable_geometries(object(), cached)
    assert requested == ["RESULT/best.xyz"]
