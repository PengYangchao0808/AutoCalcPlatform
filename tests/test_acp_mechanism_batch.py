"""Batch S3/S4 unified-engine tests (batch plan §13 acceptance criteria).

Covers: TAG parsing + priority order, multi-structure loading, S2 candidate
manifest intake (user selection wins over algorithm recommendations),
BatchConfirmEngine execution (TS/INT mixed batch, failure isolation,
resume-with-skip), unified result products, and the S3/S4 shared-logic /
different-profile contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acp.mechanism.batch_confirm import BATCH_MANIFEST_NAME, BatchConfirmEngine
from acp.mechanism.batch_models import (
    BatchCalculationManifest,
    BatchStructureItem,
    apply_user_overrides,
    build_tag_title,
    item_cache_key,
    load_batch_request,
    load_items_from_s2_candidate_manifest,
    load_items_from_s2_path_manifest,
    load_items_from_xyz_text,
    normalize_tag,
    parse_tag_comment,
)
from acp.mechanism.providers.contracts import RefinementAttempt, RefinementManifest
from acp.mechanism.stages.confirm import HighConfirmProfile, LowConfirmProfile

WATER_XYZ = "3\nwater\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n"


def _tagged_xyz(tag: str, candidate: str = "") -> str:
    title = build_tag_title(tag, candidate_id=candidate or None, source="test")
    lines = WATER_XYZ.splitlines()
    return "\n".join([lines[0], title, *lines[2:]]) + "\n"


class _FakeRefinementProvider:
    """Batch-refinement fake: fails requested ids, succeeds the rest."""

    def __init__(self, energy: float = -76.5, fail_ids: set[str] | None = None) -> None:
        self.energy = energy
        self.fail_ids = fail_ids or set()
        self.seen_requests: list[list[str]] = []

    def refine(self, requests, fidelity) -> RefinementManifest:
        from acp.mechanism.models import StationaryPoint, TsIdentity

        self.seen_requests.append([request.id for request in requests])
        attempts = []
        points = []
        for request in requests:
            if request.id in self.fail_ids:
                raise_for_request = RuntimeError(f"boom {request.id}")
                attempts.append(
                    RefinementAttempt(
                        request_id=request.id,
                        status="failed",
                        stationary_point=None,
                        evidence={"error": str(raise_for_request)},
                    )
                )
                continue
            canonical = Path(request.input_geometry.path)
            out_dir = canonical.parent / "out"
            out_dir.mkdir(parents=True, exist_ok=True)
            canonical_copy = out_dir / "canonical.xyz"
            canonical_copy.write_text(canonical.read_text(encoding="utf-8"), encoding="utf-8")
            identity = None
            if request.kind == "ts":
                identity = TsIdentity(
                    imaginary_count=1, imaginary_frequency_cm1=-1000.0, valid=True
                )
            point = StationaryPoint(
                point_id=request.id,
                role=request.role,
                kind=request.kind,
                geometry=SimpleNamespace(path=str(canonical_copy), sha256="", kind="geometry"),
                charge=request.charge,
                multiplicity=request.multiplicity,
                energy_hartree=self.energy,
                identity=identity,
                metadata={
                    "opt_status": "complete",
                    "frequency_status": "complete",
                    "sp_status": "complete",
                    "thermochemistry": {"g_composite_hartree": self.energy - 0.01},
                    "canonical_xyz": str(canonical_copy),
                },
            )
            attempts.append(
                RefinementAttempt(
                    request_id=request.id,
                    status="success",
                    stationary_point=point,
                    evidence={"canonical_xyz": str(canonical_copy)},
                )
            )
            points.append(point)
        winner = next((p for p in points if p.kind == "ts"), points[0] if points else None)
        return RefinementManifest(
            manifest_id="rm_batch_test",
            canonical_winner=winner,
            attempts=attempts,
            manifest_hash="sha256:batch",
            fidelity=str(getattr(fidelity, "name", fidelity)),
            metadata={},
        )


def _item(
    item_id: str, tag: str, candidate: str = "", xyz: str | None = None
) -> BatchStructureItem:
    return BatchStructureItem(
        item_id=item_id,
        name=item_id,
        tag=tag,
        xyz=xyz or _tagged_xyz(tag, candidate or item_id),
        candidate_id=candidate or item_id,
        source_type="upload",
        source_ref="test",
    )


# --- TAG parsing (§4) -------------------------------------------------------


def test_parse_tag_comment_case_insensitive_and_aliases() -> None:
    assert parse_tag_comment("TAG: TS | candidate_id=a")["tag"] == "TS"
    assert parse_tag_comment("TAG: ts")["tag"] == "TS"
    assert parse_tag_comment("TAG: Transition_State")["tag"] == "TS"
    assert parse_tag_comment("TAG: intermediate")["tag"] == "INT"
    assert parse_tag_comment("TAG: Minimum")["tag"] == "INT"
    assert parse_tag_comment("no tag here")["tag"] is None
    assert parse_tag_comment("TAG: junk")["tag"] is None


def test_normalize_tag_rejects_unknown() -> None:
    assert normalize_tag("ts") == "TS"
    assert normalize_tag("INT") == "INT"
    assert normalize_tag("intermediate") == "INT"
    assert normalize_tag("") is None
    assert normalize_tag("not-a-role") is None


def test_multiframe_xyz_loads_one_item_per_frame() -> None:
    text = _tagged_xyz("TS", "frameA") + _tagged_xyz("INT", "frameB") + WATER_XYZ
    items = load_items_from_xyz_text(text, base_name="upload")
    assert len(items) == 3
    assert [item.tag for item in items] == ["TS", "INT", "INT"]
    assert [item.item_id for item in items] == ["item_001", "item_002", "item_003"]


def test_untagged_structure_defaults_to_int() -> None:
    items = load_items_from_xyz_text(WATER_XYZ, base_name="plain")
    assert items[0].tag == "INT"
    assert items[0].kind == "minimum"
    assert items[0].role == "intermediate"


def test_ts_prefix_candidate_title_implies_tag() -> None:
    xyz = "3\nts_guess_001 source=x\nO 0.0 0.0 0.0\nH 0.9 0.0 0.0\nH -0.3 0.9 0.0\n"
    items = load_items_from_xyz_text(xyz, base_name="s2")
    assert items[0].tag == "TS"
    assert items[0].candidate_id == "ts_guess_001"


def test_user_override_beats_parsed_tag() -> None:
    items = load_items_from_xyz_text(_tagged_xyz("TS", "a") + _tagged_xyz("INT", "b"))
    apply_user_overrides(items, {"item_001": {"tag": "INT"}, "b": {"tag": "TS"}})
    assert [item.tag for item in items] == ["INT", "TS"]


# --- cache key + resume contract (§8) ---------------------------------------


def test_cache_key_changes_with_tag_and_geometry() -> None:
    base = _item("item_001", "TS")
    changed_tag = _item("item_001", "INT")
    changed_xyz = _item("item_001", "TS", xyz=_tagged_xyz("TS") + "")
    assert item_cache_key(base, "s3") == item_cache_key(_item("item_001", "TS"), "s3")
    assert item_cache_key(base, "s3") != item_cache_key(changed_tag, "s3")
    assert item_cache_key(base, "s3") != item_cache_key(base, "s4")
    assert item_cache_key(base, "s3") != item_cache_key(changed_xyz, "s3")


# --- batch engine (§7/§8) ---------------------------------------------------


def test_batch_engine_runs_mixed_ts_int_batch(tmp_path: Path) -> None:
    provider = _FakeRefinementProvider()
    engine = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider,
    )
    items = [_item("item_001", "TS", "ts_guess_001"), _item("item_002", "INT", "int_guess_001")]
    outcome = engine.run(items, charge=0, multiplicity=1, workflow="Lowconfirm")

    assert outcome.profile_level == "s3"
    kinds = {record.candidate_id: record.kind for record in outcome.items}
    assert kinds == {"ts_guess_001": "ts", "int_guess_001": "minimum"}
    assert all(record.status == "completed" for record in outcome.items)
    assert provider.seen_requests == [["ts_guess_001", "int_guess_001"]]

    manifest = BatchCalculationManifest.read(
        tmp_path / "RESULT" / "mechanism" / BATCH_MANIFEST_NAME
    )
    assert manifest is not None and manifest.counts["completed"] == 2

    ts_structure = tmp_path / "RESULT" / "structures" / "item_001__TAG_TS__optimized.xyz"
    assert ts_structure.is_file()
    assert "TAG: TS" in ts_structure.read_text(encoding="utf-8").splitlines()[1]

    result_manifest = json.loads(
        (tmp_path / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    products = {product["id"]: product for product in result_manifest["products"]}
    assert products["batch_item_001"]["kind"] == "structure"
    assert products["batch_item_002"]["kind"] == "structure"

    summary = json.loads(
        (tmp_path / "RESULT" / "result_summary.json").read_text(encoding="utf-8")
    )
    assert len(summary["products"]) == 2
    assert all(product["role"] == "final_stable_structure" for product in summary["products"])


def test_batch_engine_single_failure_does_not_kill_batch(tmp_path: Path) -> None:
    provider = _FakeRefinementProvider(fail_ids={"bad_one"})
    engine = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider,
    )
    items = [_item("item_001", "TS", "bad_one"), _item("item_002", "INT", "good_one")]
    outcome = engine.run(items, charge=0, multiplicity=1)
    by_id = {record.candidate_id: record for record in outcome.items}
    assert by_id["bad_one"].status == "failed"
    assert "boom" in by_id["bad_one"].error
    assert by_id["good_one"].status == "completed"


def test_batch_engine_resume_skips_completed_items(tmp_path: Path) -> None:
    provider = _FakeRefinementProvider()
    engine = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider,
    )
    items = [_item("item_001", "TS", "ts_1"), _item("item_002", "INT", "int_1")]
    first = engine.run(items, charge=0, multiplicity=1)
    assert first.manifest.counts["completed"] == 2

    provider2 = _FakeRefinementProvider()
    engine2 = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider2,
    )
    second = engine.run(items, charge=0, multiplicity=1) if False else engine2.run(
        items, charge=0, multiplicity=1
    )
    assert provider2.seen_requests == []  # everything carried, nothing re-executed
    assert second.manifest.counts["skipped"] == 2
    assert second.confirm is None
    assert all(record.status == "skipped" for record in second.items)


def test_batch_engine_resume_retries_only_failed_items(tmp_path: Path) -> None:
    provider = _FakeRefinementProvider(fail_ids={"flaky"})
    engine = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider,
    )
    items = [_item("item_001", "TS", "flaky"), _item("item_002", "INT", "solid")]
    engine.run(items, charge=0, multiplicity=1)

    provider2 = _FakeRefinementProvider()
    engine2 = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider2,
    )
    second = engine2.run(items, charge=0, multiplicity=1)
    assert provider2.seen_requests == [["flaky"]]
    by_id = {record.candidate_id: record for record in second.items}
    assert by_id["flaky"].status == "completed"
    assert by_id["solid"].status == "skipped"


def test_s3_s4_same_logic_different_profile(tmp_path: Path) -> None:
    provider_s3 = _FakeRefinementProvider()
    provider_s4 = _FakeRefinementProvider()
    items = [_item("item_001", "TS", "ts_1")]
    BatchConfirmEngine(
        work_root=tmp_path / "s3" / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=provider_s3,
    ).run(items, charge=0, multiplicity=1, workflow="Lowconfirm")
    BatchConfirmEngine(
        work_root=tmp_path / "s4" / "WORK" / "03_OPT",
        profile=HighConfirmProfile(run_irc=False),
        refinement_provider=provider_s4,
    ).run(items, charge=0, multiplicity=1, workflow="Highconfirm")

    batch_dir = tmp_path / "s4" / "RESULT" / "mechanism"
    s3_manifest = BatchCalculationManifest.read(
        tmp_path / "s3" / "RESULT" / "mechanism" / BATCH_MANIFEST_NAME
    )
    s4_manifest = BatchCalculationManifest.read(batch_dir / BATCH_MANIFEST_NAME)
    assert s3_manifest is not None and s3_manifest.profile_level == "s3"
    assert s4_manifest is not None and s4_manifest.profile_level == "s4"
    assert s3_manifest.items[0].status == s4_manifest.items[0].status == "completed"
    # Same cache key inputs must differ across profiles (no cross-profile reuse).
    assert item_cache_key(items[0], "s3") != item_cache_key(items[0], "s4")


def test_batch_engine_per_item_directories(tmp_path: Path) -> None:
    engine = BatchConfirmEngine(
        work_root=tmp_path / "WORK" / "03_OPT",
        profile=LowConfirmProfile(run_irc=False),
        refinement_provider=_FakeRefinementProvider(),
    )
    items = [_item("item_001", "TS", "a"), _item("item_002", "INT", "b")]
    engine.run(items, charge=0, multiplicity=1)
    assert (tmp_path / "WORK" / "03_OPT" / "batch" / "item_001" / "input.xyz").is_file()
    assert (tmp_path / "WORK" / "03_OPT" / "batch" / "item_002" / "input.xyz").is_file()


# --- unified input protocol (§3) --------------------------------------------


def _write_s2_job(tmp_path: Path, confirmed: bool = True) -> Path:
    """Build a v2 S2 job tree with a materialized candidate manifest."""
    s2_payload = {
        "schema_version": "s2_path_v2",
        "schema": "s2_path_v2",
        "workflow": "PESsearch",
        "stage": "S2",
        "mode": "bond_length_scan",
        "charge": 0,
        "multiplicity": 1,
        "scan": {"scan_dir": "WORK/02_SEARCH/s2_bond_scan_001", "frames": []},
        "recommendations": {
            "ts": [{"candidate_id": "ts_001", "geometry_path": "scan_frames/frame_001.xyz"}],
            "intermediates": [
                {"candidate_id": "int_001", "geometry_path": "scan_frames/frame_002.xyz"}
            ],
        },
        "review": {"required": True, "status": "confirmed" if confirmed else "pending"},
    }
    mechanism_dir = tmp_path / "RESULT" / "mechanism"
    mechanism_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = tmp_path / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "scan_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_001.xyz").write_text(WATER_XYZ, encoding="utf-8")
    (frames_dir / "frame_002.xyz").write_text(WATER_XYZ, encoding="utf-8")
    (mechanism_dir / "s2_path_manifest.json").write_text(
        json.dumps(s2_payload), encoding="utf-8"
    )
    structures_dir = tmp_path / "RESULT" / "structures" / "s2_candidates"
    structures_dir.mkdir(parents=True, exist_ok=True)
    (structures_dir / "user_ts.xyz").write_text(_tagged_xyz("TS", "user_ts"), encoding="utf-8")
    (mechanism_dir / "s2_candidate_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "s2_candidate_v1",
                "candidates": [
                    {
                        "candidate_id": "user_ts",
                        "frame_index": 3,
                        "role": "ts",
                        "geometry": "structures/s2_candidates/user_ts.xyz",
                        "active": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return mechanism_dir / "s2_path_manifest.json"


def test_s2_candidate_manifest_selection_wins_over_recommendations(tmp_path: Path) -> None:
    manifest = _write_s2_job(tmp_path)
    items, payload = load_items_from_s2_path_manifest(manifest)
    assert [item.candidate_id for item in items] == ["user_ts"]
    assert items[0].tag == "TS"
    assert payload["schema_version"] == "s2_path_v2"


def test_s2_candidate_manifest_loads_all_active_candidates_by_default(tmp_path: Path) -> None:
    manifest = _write_s2_job(tmp_path)
    candidate_manifest = manifest.with_name("s2_candidate_manifest.json")
    payload = json.loads(candidate_manifest.read_text(encoding="utf-8"))
    structures_dir = tmp_path / "RESULT" / "structures" / "s2_candidates"
    (structures_dir / "user_int.xyz").write_text(_tagged_xyz("INT", "user_int"), encoding="utf-8")
    (structures_dir / "inactive.xyz").write_text(_tagged_xyz("TS", "inactive"), encoding="utf-8")
    payload["candidates"].extend(
        [
            {
                "candidate_id": "user_int",
                "frame_index": 4,
                "role": "intermediate",
                "geometry": "structures/s2_candidates/user_int.xyz",
                "active": True,
            },
            {
                "candidate_id": "inactive",
                "frame_index": 5,
                "role": "ts",
                "geometry": "structures/s2_candidates/inactive.xyz",
                "active": False,
            },
        ]
    )
    candidate_manifest.write_text(json.dumps(payload), encoding="utf-8")

    items = load_items_from_s2_candidate_manifest(candidate_manifest)

    assert [item.candidate_id for item in items] == ["user_ts", "user_int"]
    assert [item.tag for item in items] == ["TS", "INT"]


def test_s2_candidate_manifest_explicit_candidate_ids(tmp_path: Path) -> None:
    manifest = _write_s2_job(tmp_path)
    items = load_items_from_s2_candidate_manifest(
        manifest.with_name("s2_candidate_manifest.json"), ["user_ts"]
    )
    assert [item.candidate_id for item in items] == ["user_ts"]
    with pytest.raises(ValueError, match="Unknown candidate ids"):
        load_items_from_s2_candidate_manifest(
            manifest.with_name("s2_candidate_manifest.json"), ["ghost"]
        )


def test_s2_recommendation_fallback_without_candidate_manifest(tmp_path: Path) -> None:
    manifest = _write_s2_job(tmp_path)
    (manifest.with_name("s2_candidate_manifest.json")).unlink()
    items, _payload = load_items_from_s2_path_manifest(manifest)
    assert [item.candidate_id for item in items] == ["ts_001"]  # TS-first default


def test_load_batch_request_mixed_sources(tmp_path: Path) -> None:
    manifest = _write_s2_job(tmp_path / "s2job")
    xyz_file = tmp_path / "upload.xyz"
    xyz_file.write_text(_tagged_xyz("INT", "up1") + WATER_XYZ, encoding="utf-8")
    request = {
        "schema_version": "batch_structures_v1",
        "items": [
            {
                "source_type": "s2_candidates",
                "manifest": str(manifest),
                "candidate_ids": ["user_ts"],
            },
            {"source_type": "file", "path": str(xyz_file)},
            {"name": "pasted", "tag": "TS", "xyz": WATER_XYZ},
        ],
        "overrides": {"item_004": {"tag": "INT"}},
    }
    items = load_batch_request(request)
    assert [item.candidate_id for item in items] == ["user_ts", "up1", "item_003", "item_004"]
    assert [item.tag for item in items] == ["TS", "INT", "INT", "INT"]


def test_load_batch_request_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no structure entries"):
        load_batch_request({"items": []})
    with pytest.raises(ValueError, match="no included structures"):
        load_batch_request({"items": [{"xyz": WATER_XYZ, "include": False}]})


# --- stage integration (§13) ------------------------------------------------


def test_run_low_confirm_direct_structures(tmp_path: Path) -> None:
    from acp.mechanism.stages import run_low_confirm

    payload = run_low_confirm(
        output_dir=tmp_path / "low",
        structures=[_item("item_001", "TS", "up_ts"), _item("item_002", "INT", "up_int")],
        run_irc=False,
        refinement_provider=_FakeRefinementProvider(),
    )
    assert payload["schema_version"] == "s3_lowconfirm_v1"
    assert payload["source"]["kind"] == "batch_structures"
    assert payload["gates"]["G3"] == "PASS"
    assert (tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json").is_file()
    assert (tmp_path / "low" / "RESULT" / "mechanism" / BATCH_MANIFEST_NAME).is_file()
    ts_row = next(row for row in payload["candidates"] if row["kind"] == "ts")
    assert ts_row["id"] == "up_ts"


def test_run_high_confirm_from_s3_manifest_unchanged_contract(tmp_path: Path) -> None:
    from acp.mechanism.stages import run_high_confirm, run_low_confirm

    low = run_low_confirm(
        output_dir=tmp_path / "low",
        structures=[_item("item_001", "TS", "ts_1")],
        run_irc=False,
        refinement_provider=_FakeRefinementProvider(),
    )
    assert low["gates"]["G3"] == "PASS"
    s3_manifest = tmp_path / "low" / "RESULT" / "mechanism" / "s3_lowconfirm_manifest.json"
    high = run_high_confirm(
        from_manifest=s3_manifest,
        output_dir=tmp_path / "high",
        refinement_provider=_FakeRefinementProvider(energy=-76.8),
    )
    assert high["schema_version"] == "s4_highconfirm_v1"
    assert high["gates"]["G5"] == "PASS"
    assert (tmp_path / "high" / "RESULT" / "mechanism" / "mechanism_profile.json").is_file()
    assert high["transition_states"] if "transition_states" in high else True
    profile = json.loads(
        (tmp_path / "high" / "RESULT" / "mechanism" / "mechanism_profile.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile["transition_states"][0]["id"] == "ts_1"


def test_run_low_confirm_uses_confirmed_s2_candidates_only(tmp_path: Path) -> None:
    from acp.mechanism.stages import run_low_confirm

    manifest = _write_s2_job(tmp_path)
    payload = run_low_confirm(
        from_manifest=manifest,
        output_dir=tmp_path / "low",
        run_irc=False,
        refinement_provider=_FakeRefinementProvider(),
    )
    ids = [row["id"] for row in payload["candidates"]]
    assert ids == ["user_ts"]  # unselected recommendations never enter the batch


def test_run_low_confirm_pending_review_still_gated(tmp_path: Path) -> None:
    from acp.mechanism.stages import run_low_confirm

    manifest = _write_s2_job(tmp_path, confirmed=False)
    with pytest.raises(ValueError, match="not yet confirmed"):
        run_low_confirm(
            from_manifest=manifest,
            output_dir=tmp_path / "low",
            refinement_provider=_FakeRefinementProvider(),
        )


# --- PESsearch result products (§5/§6) --------------------------------------


def test_pes_search_registers_structure_products(tmp_path: Path) -> None:
    from acp.mechanism.stages import pes_search as ps
    from tests.test_acp_mechanism_stages import _confsearch_manifest, _fake_path_result

    conf_manifest = _confsearch_manifest(tmp_path / "source_job")
    import unittest.mock as mock

    with mock.patch.object(
        ps,
        "_build_path_strategy",
        lambda *a, **k: SimpleNamespace(search=lambda *a, **k: _fake_path_result()),
    ):
        ps.run_pes_search(
            from_manifest=conf_manifest,
            output_dir=tmp_path / "pes",
            strategy="guided-scan",
            coordinate_plan={
                "coordinates": [
                    {"id": "rc1", "kind": "distance", "atoms": [0, 1], "start": 2.0, "end": 1.0}
                ],
                "points": 21,
            },
        )

    result_manifest = json.loads(
        (tmp_path / "pes" / "RESULT" / "result_manifest.json").read_text(encoding="utf-8")
    )
    structure_products = [
        product
        for product in result_manifest["products"]
        if product["kind"] == "structure"
    ]
    assert structure_products, "PESsearch candidates must register as structure products"
    assert all(product["id"].startswith("s2_candidate_") for product in structure_products)

    ts_xyz = tmp_path / "pes" / "RESULT" / "mechanism" / "ts_guesses" / "ts_guess_001.xyz"
    assert ts_xyz.is_file()
    comment = ts_xyz.read_text(encoding="utf-8").splitlines()[1]
    parsed = parse_tag_comment(comment)
    assert parsed["tag"] == "TS"
    assert parsed["candidate_id"] == "ts_guess_001"

    summary = json.loads(
        (tmp_path / "pes" / "RESULT" / "result_summary.json").read_text(encoding="utf-8")
    )
    assert summary["products"], "legacy summary pointer must exist for PESsearch"
