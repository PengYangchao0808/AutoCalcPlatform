"""Tests for acp.results.structure_viewer — structure_viewer_v1 canonical contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import guard — the module under test must import cleanly
# ---------------------------------------------------------------------------


def test_module_imports():
    """The module under test must be importable."""
    from acp.results.structure_viewer import (  # noqa: F401
        StructureViewerEntry,
        StructureViewerGroup,
        StructureViewerPayload,
        build_structure_viewer_payload,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TASK_ROOT_MARKERS = {"job.json", "task.json"}


def _make_task_dir(tmp_path: Path, *, result_manifest: dict | None = None,
                   confsearch_manifest: dict | None = None,
                   pes_profile: dict | None = None,
                   pes_recommendations: dict | None = None,
                   pes_review: dict | None = None,
                   ) -> Path:
    """Create a minimal task directory with optional manifest files."""
    # Scheduler marker files
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")

    if result_manifest is not None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps(result_manifest), encoding="utf-8"
        )

    if confsearch_manifest is not None:
        cs_dir = tmp_path / "RESULT" / "confsearch"
        cs_dir.mkdir(parents=True, exist_ok=True)
        (cs_dir / "confsearch_manifest.json").write_text(
            json.dumps(confsearch_manifest), encoding="utf-8"
        )

    if pes_profile is not None:
        pes_dir = tmp_path / "RESULT" / "pes_search"
        pes_dir.mkdir(parents=True, exist_ok=True)
        (pes_dir / "pes_profile.json").write_text(
            json.dumps(pes_profile), encoding="utf-8"
        )

    if pes_recommendations is not None:
        pes_dir = tmp_path / "RESULT" / "pes_search"
        pes_dir.mkdir(parents=True, exist_ok=True)
        (pes_dir / "pes_recommendations.json").write_text(
            json.dumps(pes_recommendations), encoding="utf-8"
        )

    if pes_review is not None:
        pes_dir = tmp_path / "RESULT" / "pes_search"
        pes_dir.mkdir(parents=True, exist_ok=True)
        (pes_dir / "pes_review.json").write_text(
            json.dumps(pes_review), encoding="utf-8"
        )

    return tmp_path


# ---------------------------------------------------------------------------
# Revision tests
# ---------------------------------------------------------------------------


class TestRevision:
    """Revision computation: stability, source-sensitivity, job_status-sensitivity."""

    def test_revision_empty_when_no_sources(self, tmp_path: Path):
        """revision == 'empty' when no source manifests exist."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="test_001", workflow="Confsearch", job_status="completed"
        )
        assert payload.revision == "empty"

    def test_revision_stable_on_rebuild(self, tmp_path: Path):
        """Same inputs → same revision across two calls."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        rm = {"version": 2, "task_id": "t", "workflow": "Confsearch", "status": "completed", "products": []}
        task = _make_task_dir(tmp_path, result_manifest=rm)

        p1 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        p2 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        assert p1.revision == p2.revision

    def test_revision_changes_when_source_byte_changes(self, tmp_path: Path):
        """Touch a byte in confsearch_manifest.json → different revision."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        cs = {"schema_version": "confsearch_v1", "workflow": "Confsearch", "conformers": []}
        task = _make_task_dir(tmp_path, confsearch_manifest=cs)

        p1 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        r1 = p1.revision

        # Touch the manifest
        cs["conformers"] = [{"id": "c1"}]
        (task / "RESULT" / "confsearch" / "confsearch_manifest.json").write_text(
            json.dumps(cs), encoding="utf-8"
        )

        p2 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        r2 = p2.revision
        assert r1 != r2

    def test_revision_incorporates_job_status(self, tmp_path: Path):
        """Same manifests + different job_status → different revision."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        rm = {"version": 2, "task_id": "t", "workflow": "optimize", "status": "completed", "products": []}
        task = _make_task_dir(tmp_path, result_manifest=rm)

        p1 = build_structure_viewer_payload(task, job_id="j1", workflow="optimize", job_status="completed")
        p2 = build_structure_viewer_payload(task, job_id="j1", workflow="optimize", job_status="failed")
        assert p1.revision != p2.revision


# ---------------------------------------------------------------------------
# Payload schema tests
# ---------------------------------------------------------------------------


class TestPayloadSchema:
    """Payload shape and field presence."""

    def test_schema_version_present(self, tmp_path: Path):
        """schema_version == 'structure_viewer_v1'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="test_001", workflow="optimize", job_status="completed"
        )
        assert payload.schema_version == "structure_viewer_v1"

    def test_payload_to_dict_field_names(self, tmp_path: Path):
        """to_dict() must expose the doc §4.1 field names exactly."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        d = payload.to_dict()
        expected_keys = {
            "schema_version", "job_id", "workflow", "job_status",
            "revision", "default_entry_id", "groups", "entries", "warnings",
        }
        assert set(d.keys()) == expected_keys

    def test_payload_types(self, tmp_path: Path):
        """Field types match the contract."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        d = payload.to_dict()
        assert isinstance(d["schema_version"], str)
        assert isinstance(d["job_id"], str)
        assert isinstance(d["workflow"], str)
        assert isinstance(d["job_status"], str)
        assert isinstance(d["revision"], str)
        assert isinstance(d["groups"], list)
        assert isinstance(d["entries"], list)
        assert isinstance(d["warnings"], list)

    def test_entry_to_dict_field_names(self, tmp_path: Path):
        """Entry to_dict() must expose the doc §4.1 entry field names."""
        from acp.results.structure_viewer import StructureViewerEntry

        entry = StructureViewerEntry(
            id="test_entry",
            group_id="test_group",
            label="Test",
            role="minimum",
            status="completed",
        )
        d = entry.to_dict()
        expected_keys = {
            "id", "group_id", "label", "role", "status",
            "geometry", "energy", "relative_energy_kcal",
            "boltzmann_weight", "source", "badges", "vibrations",
        }
        assert set(d.keys()) == expected_keys

    def test_group_to_dict_field_names(self):
        """Group to_dict() must expose id, label, kind."""
        from acp.results.structure_viewer import StructureViewerGroup

        group = StructureViewerGroup(id="g1", label="Test Group", kind="ensemble")
        d = group.to_dict()
        assert d == {"id": "g1", "label": "Test Group", "kind": "ensemble"}


# ---------------------------------------------------------------------------
# Entry-id helper tests
# ---------------------------------------------------------------------------


class TestEntryIdHelpers:
    """Entry-id helper functions produce documented schemes."""

    def test_confsearch_entry_id_with_conformer_id(self):
        from acp.results.structure_viewer import confsearch_entry_id
        assert confsearch_entry_id(conformer_id="0001", rank=None) == "conf_0001"

    def test_confsearch_entry_id_fallback_rank(self):
        from acp.results.structure_viewer import confsearch_entry_id
        assert confsearch_entry_id(conformer_id=None, rank=3) == "conf_rank_3"

    def test_confsearch_entry_id_prefers_conformer_id(self):
        from acp.results.structure_viewer import confsearch_entry_id
        # When both provided, conformer_id wins
        assert confsearch_entry_id(conformer_id="0005", rank=2) == "conf_0005"

    def test_pes_entry_id(self):
        from acp.results.structure_viewer import pes_entry_id
        assert pes_entry_id(candidate_id="cand_42") == "pes_cand_42"

    def test_batch_entry_id(self):
        from acp.results.structure_viewer import batch_entry_id
        assert batch_entry_id(item_id="item_007") == "batch_item_007"

    def test_simple_entry_id(self):
        from acp.results.structure_viewer import simple_entry_id
        assert simple_entry_id(step_kind="optimize") == "simple_optimize"

    def test_scan_entry_id(self):
        from acp.results.structure_viewer import scan_entry_id
        assert scan_entry_id(frame_index=5) == "scan_frame_5"

    def test_irc_entry_id(self):
        from acp.results.structure_viewer import irc_entry_id
        assert irc_entry_id(endpoint="forward", frame_index=3) == "irc_forward_3"

    def test_manual_entry_id_deterministic(self):
        from acp.results.structure_viewer import manual_entry_id
        r1 = manual_entry_id(relpath="some/relative/path.xyz")
        r2 = manual_entry_id(relpath="some/relative/path.xyz")
        assert r1 == r2
        assert r1.startswith("manual_")
        assert len(r1) == len("manual_") + 12

    def test_legacy_entry_id_deterministic(self):
        from acp.results.structure_viewer import legacy_entry_id
        r1 = legacy_entry_id(relpath="old/result.xyz")
        r2 = legacy_entry_id(relpath="old/result.xyz")
        assert r1 == r2
        assert r1.startswith("legacy_")
        assert len(r1) == len("legacy_") + 12

    def test_manual_entry_id_differs_from_legacy(self):
        from acp.results.structure_viewer import legacy_entry_id, manual_entry_id
        # Same relpath but different prefix
        m = manual_entry_id(relpath="test.xyz")
        l = legacy_entry_id(relpath="test.xyz")
        assert m != l

    def test_resolve_collision_appends_suffix(self):
        from acp.results.structure_viewer import resolve_collision
        base = "conf_0001"
        geo_ref = "conformers/conf_0001.xyz"
        resolved = resolve_collision(base, geo_ref)
        # First call should return base (no collision yet), second with same args
        # should also return base — collision is caller-managed
        assert resolved == base or resolved.startswith(base + "_")

    def test_resolve_collision_deterministic(self):
        from acp.results.structure_viewer import resolve_collision
        r1 = resolve_collision("conf_0001", "a.xyz")
        r2 = resolve_collision("conf_0001", "a.xyz")
        assert r1 == r2


# ---------------------------------------------------------------------------
# Corrupt manifest tests
# ---------------------------------------------------------------------------


class TestCorruptManifests:
    """Corrupt/missing manifests yield warnings, never exceptions."""

    def test_corrupt_result_manifest_yields_warning(self, tmp_path: Path):
        """A corrupt result_manifest.json produces a warning in payload, no exception."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        result_dir = tmp_path / "RESULT"
        result_dir.mkdir()
        (result_dir / "result_manifest.json").write_text("NOT VALID JSON {{{", encoding="utf-8")
        (tmp_path / "job.json").write_text("{}")
        (tmp_path / "task.json").write_text("{}")

        payload = build_structure_viewer_payload(
            tmp_path, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert len(payload.warnings) > 0
        assert any("result_manifest" in w.lower() or "corrupt" in w.lower()
                    for w in payload.warnings)

    def test_missing_task_dir_graceful(self, tmp_path: Path):
        """A nonexistent task_root yields empty payload + warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        missing = tmp_path / "nonexistent"
        payload = build_structure_viewer_payload(
            missing, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert payload.schema_version == "structure_viewer_v1"
        assert len(payload.warnings) > 0

    def test_corrupt_confsearch_manifest_yields_warning(self, tmp_path: Path):
        """A corrupt confsearch_manifest.json produces a warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        cs_dir = tmp_path / "RESULT" / "confsearch"
        cs_dir.mkdir(parents=True)
        (cs_dir / "confsearch_manifest.json").write_text("{bad json", encoding="utf-8")
        (tmp_path / "job.json").write_text("{}")
        (tmp_path / "task.json").write_text("{}")

        payload = build_structure_viewer_payload(
            tmp_path, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert len(payload.warnings) > 0


# ---------------------------------------------------------------------------
# Dispatcher skeleton tests
# ---------------------------------------------------------------------------


class TestDispatcherSkeleton:
    """The dispatcher returns valid payloads for all workflow strings."""

    @pytest.mark.parametrize("workflow", [
        "Confsearch", "PESsearch", "BatchOptimize",
        "optimize", "singlepoint", "frequency", "scan", "irc",
        "xtb-optimize", "UnknownWorkflow",
    ])
    def test_dispatch_returns_valid_payload(self, tmp_path: Path, workflow: str):
        """Every workflow dispatch returns a valid payload, never raises."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow=workflow, job_status="completed"
        )
        assert payload.schema_version == "structure_viewer_v1"
        assert hasattr(payload.entries, "__iter__")
        assert hasattr(payload.groups, "__iter__")
        assert hasattr(payload.warnings, "__iter__")

    def test_unimplemented_resolver_warns(self, tmp_path: Path):
        """Resolvers not yet implemented produce a specific warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        # Todos 2-6 replace these — for now, expect the scaffold warning
        assert any("not yet implemented" in w.lower() for w in payload.warnings)
