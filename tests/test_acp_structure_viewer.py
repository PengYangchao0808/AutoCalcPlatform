"""Tests for acp.results.structure_viewer — structure_viewer_v1 canonical contract."""

from __future__ import annotations

import hashlib
import json
import math
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

    def test_scan_no_trajectory_warns(self, tmp_path: Path):
        """Scan with no trajectory produces a warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        assert any("no scan trajectory" in w.lower() for w in payload.warnings)


# ---------------------------------------------------------------------------
# Confsearch resolver tests
# ---------------------------------------------------------------------------


def _confsearch_manifest(*, conformers: list[dict], temperature_k: float | None = None) -> dict:
    """Build a realistic confsearch_manifest.json fixture."""
    payload: dict = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "protocol": "censo-crest",
        "profile": "default",
        "refinement_policy": "screen",
        "backend": "native",
        "input": {},
        "sampling": {},
        "conformers": conformers,
        "selected_conformers": [c["conf_id"] for c in conformers[:1]],
        "refinement": {},
        "provenance": {},
        "quality_gates": {},
    }
    if temperature_k is not None:
        payload["temperature_k"] = temperature_k
    return payload


def _conf(*, conf_id: str, rank: int, energy: float, gibbs: float | None = None,
          weight: float | None = None, relative: float | None = None) -> dict:
    """Build one conformer dict matching the ConformerEntry.to_dict() shape."""
    entry: dict = {
        "conf_id": conf_id,
        "geometry": f"conformers/{conf_id}.xyz",
        "energy_hartree": energy,
        "free_energy_hartree": gibbs,
        "relative_energy_kcal": relative,
        "boltzmann_weight": weight,
        "rank": rank,
    }
    return entry


class TestConfsearchResolver:
    """Confsearch resolver: entries, ΔE, weights, manifest order, default selection."""

    def test_confsearch_entries_basic(self, tmp_path: Path):
        """3-conformer fixture → 3 entries, correct ids, roles, group."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, gibbs=-99.5, weight=0.7, relative=0.0),
            _conf(conf_id="0002", rank=2, energy=-99.8, gibbs=-99.3, weight=0.2, relative=1.25),
            _conf(conf_id="0003", rank=3, energy=-99.6, gibbs=-99.1, weight=0.1, relative=2.51),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )

        assert len(payload.entries) == 3
        assert payload.entries[0].id == "conf_0001"
        assert payload.entries[1].id == "conf_0002"
        assert payload.entries[2].id == "conf_0003"
        for entry in payload.entries:
            assert entry.role == "minimum"
            assert entry.group_id == "final_conformers"
        assert any(g.id == "final_conformers" for g in payload.groups)

    def test_confsearch_default_is_rank1(self, tmp_path: Path):
        """default_entry_id = rank-1 conformer id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
            _conf(conf_id="0002", rank=2, energy=-99.8, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert payload.default_entry_id == "conf_0001"

    def test_confsearch_selected_badge_on_rank1(self, tmp_path: Path):
        """Rank-1 entry carries 'selected' and 'rank-1' badges."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.6),
            _conf(conf_id="0002", rank=2, energy=-99.5, weight=0.4),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        rank1 = payload.entries[0]
        assert "selected" in rank1.badges
        assert "rank-1" in rank1.badges

    def test_confsearch_rank_badges(self, tmp_path: Path):
        """Each entry carries 'rank-<n>' badge."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
            _conf(conf_id="0002", rank=2, energy=-99.8, weight=0.3),
            _conf(conf_id="0003", rank=3, energy=-99.5, weight=0.2),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert "rank-1" in payload.entries[0].badges
        assert "rank-2" in payload.entries[1].badges
        assert "rank-3" in payload.entries[2].badges

    def test_confsearch_relative_energy_kcal(self, tmp_path: Path):
        """relative_energy_kcal computed vs group minimum."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
            _conf(conf_id="0002", rank=2, energy=-99.8, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert payload.entries[0].relative_energy_kcal == 0.0
        assert payload.entries[1].relative_energy_kcal is not None
        assert payload.entries[1].relative_energy_kcal > 0.0

    def test_confsearch_energy_kind_gibbs(self, tmp_path: Path):
        """energy.kind = 'gibbs' when free_energy_hartree present."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, gibbs=-99.5, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert payload.entries[0].energy.kind == "gibbs"
        assert payload.entries[0].energy.value == -99.5

    def test_confsearch_energy_kind_electronic(self, tmp_path: Path):
        """energy.kind = 'electronic' when only energy_hartree present."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert payload.entries[0].energy.kind == "electronic"
        assert payload.entries[0].energy.value == -100.0

    def test_confsearch_weights_sum_to_one(self, tmp_path: Path):
        """Boltzmann weights from manifest sum to 1.0 within 1e-6."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.7),
            _conf(conf_id="0002", rank=2, energy=-99.8, weight=0.2),
            _conf(conf_id="0003", rank=3, energy=-99.5, weight=0.1),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        total = sum(e.boltzmann_weight for e in payload.entries if e.boltzmann_weight is not None)
        assert abs(total - 1.0) < 1e-6

    def test_confsearch_missing_weights_computed(self, tmp_path: Path):
        """When boltzmann_weight absent, computed from energies → sums to 1."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0),
            _conf(conf_id="0002", rank=2, energy=-99.8),
            _conf(conf_id="0003", rank=3, energy=-99.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        for entry in payload.entries:
            assert entry.boltzmann_weight is not None
        total = sum(e.boltzmann_weight for e in payload.entries)
        assert abs(total - 1.0) < 1e-6

    def test_confsearch_missing_weights_warning(self, tmp_path: Path):
        """Missing weights → a warning about fallback computation."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0),
            _conf(conf_id="0002", rank=2, energy=-99.8),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert any("boltzmann" in w.lower() or "weight" in w.lower() for w in payload.warnings)

    def test_confsearch_manifest_order_preserved(self, tmp_path: Path):
        """Entries appear in manifest order, NOT reordered by energy."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-99.0, weight=0.1),
            _conf(conf_id="0002", rank=2, energy=-100.0, weight=0.5),
            _conf(conf_id="0003", rank=3, energy=-99.5, weight=0.4),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        ids = [e.id for e in payload.entries]
        assert ids == ["conf_0001", "conf_0002", "conf_0003"]

    def test_confsearch_missing_manifest_yields_no_entries(self, tmp_path: Path):
        """No confsearch manifest → 0 Confsearch entries + warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        confsearch_entries = [e for e in payload.entries if e.id.startswith("conf_")]
        assert len(confsearch_entries) == 0
        assert len(payload.warnings) > 0

    def test_confsearch_source_fields(self, tmp_path: Path):
        """source.kind = formal_result, geometry_ref = RESULT/confsearch/conformers/..."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.source.kind == "formal_result"
        assert entry.source.geometry_ref is not None
        assert "conformers/0001.xyz" in entry.source.geometry_ref

    def test_confsearch_geometry_endpoint(self, tmp_path: Path):
        """geometry.endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        entry = payload.entries[0]
        assert "j1" in entry.geometry.endpoint
        assert "conf_0001" in entry.geometry.endpoint

    def test_confsearch_temperature_k_propagated(self, tmp_path: Path):
        """temperature_k from manifest → energy.temperature_k."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, gibbs=-99.5, weight=0.5),
        ]
        task = _make_task_dir(tmp_path, confsearch_manifest=_confsearch_manifest(
            conformers=conformers, temperature_k=350.0
        ))
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert payload.entries[0].energy.temperature_k == 350.0


# ---------------------------------------------------------------------------
# PES resolver tests
# ---------------------------------------------------------------------------


def _pes_recommendation(*, candidate_id: str, kind: str = "ts", frame_index: int = 0,
                        geometry_path: str = "frame_000.xyz", score: float = 0.9,
                        confidence: str = "high") -> dict:
    return {
        "candidate_id": candidate_id,
        "kind": kind,
        "frame_index": frame_index,
        "geometry_path": geometry_path,
        "score": score,
        "confidence": confidence,
        "evidence": {},
        "reason": "",
    }


def _pes_review_entry(*, candidate_id: str, frame_index: int, role: str = "TS",
                      name: str = "") -> dict:
    return {
        "candidate_id": candidate_id,
        "frame_index": frame_index,
        "role": role,
        "name": name or candidate_id,
        "selection_source": "manual",
        "structure_path": f"structures/{candidate_id}.xyz",
    }


class TestPesResolver:
    """PES resolver: two groups, source.kind, badges, default selection."""

    def test_pes_two_groups(self, tmp_path: Path):
        """Profile + recommendations + review → two strictly separated groups."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [_pes_recommendation(candidate_id="ts_frame_005", confidence="high")]
        review_entries = [_pes_review_entry(candidate_id="ts_frame_005", frame_index=5)]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []},
                              pes_review={"schema_version": "pes_review_v1", "job_id": "j1", "status": "confirmed", "revision": 1, "selected": review_entries})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        group_ids = {g.id for g in payload.groups}
        assert "pes_recommendations" in group_ids
        assert "pes_confirmed" in group_ids
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        conf_entries = [e for e in payload.entries if e.group_id == "pes_confirmed"]
        assert len(rec_entries) == 1
        assert len(conf_entries) == 1

    def test_pes_source_kind_recommendation(self, tmp_path: Path):
        """Recommendation entries have source.kind = 'algorithm_recommendation'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [_pes_recommendation(candidate_id="ts_frame_005")]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        assert len(rec_entries) == 1
        assert rec_entries[0].source.kind == "algorithm_recommendation"

    def test_pes_source_kind_confirmed(self, tmp_path: Path):
        """Confirmed entries have source.kind = 'manual_review'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        review_entries = [_pes_review_entry(candidate_id="ts_frame_005", frame_index=5)]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": [], "intermediates": []},
                              pes_review={"schema_version": "pes_review_v1", "job_id": "j1", "status": "confirmed", "revision": 1, "selected": review_entries})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        conf_entries = [e for e in payload.entries if e.group_id == "pes_confirmed"]
        assert len(conf_entries) == 1
        assert conf_entries[0].source.kind == "manual_review"

    def test_pes_recommendation_confirmed_false_flag(self, tmp_path: Path):
        """Recommendation entries carry confirmed=False machine-readable flag."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [_pes_recommendation(candidate_id="ts_frame_005")]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        assert rec_entries[0].source.confirmed is False
        d = rec_entries[0].source.to_dict()
        assert d["confirmed"] is False

    def test_pes_recommendation_badge_unconfirmed(self, tmp_path: Path):
        """Recommendation entries carry badge '未确认'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [_pes_recommendation(candidate_id="ts_frame_005")]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        assert "未确认" in rec_entries[0].badges

    def test_pes_default_highest_confidence_ts(self, tmp_path: Path):
        """Default = highest-confidence TS recommendation."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [
            _pes_recommendation(candidate_id="ts_frame_001", confidence="low", score=0.3),
            _pes_recommendation(candidate_id="ts_frame_005", confidence="high", score=0.9),
            _pes_recommendation(candidate_id="ts_frame_010", confidence="medium", score=0.6),
        ]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert payload.default_entry_id == "pes_ts_frame_005"

    def test_pes_default_highest_energy_peak_when_no_ts(self, tmp_path: Path):
        """No TS recs → default = highest-energy-peak (max score) intermediate."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        ints = [
            _pes_recommendation(candidate_id="int_frame_003", kind="intermediate", score=0.5),
            _pes_recommendation(candidate_id="int_frame_008", kind="intermediate", score=0.9),
        ]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": [], "intermediates": ints})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert payload.default_entry_id == "pes_int_frame_008"

    def test_pes_default_first_confirmed_when_no_recs(self, tmp_path: Path):
        """No recommendations → default = first confirmed entry."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        review_entries = [
            _pes_review_entry(candidate_id="ts_frame_005", frame_index=5),
            _pes_review_entry(candidate_id="int_frame_010", frame_index=10, role="INT"),
        ]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": [], "intermediates": []},
                              pes_review={"schema_version": "pes_review_v1", "job_id": "j1", "status": "confirmed", "revision": 1, "selected": review_entries})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert payload.default_entry_id == "pes_ts_frame_005"

    def test_pes_review_missing_confirmed_absent(self, tmp_path: Path):
        """Review missing → confirmed group absent, recommendations still present."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        recs = [_pes_recommendation(candidate_id="ts_frame_005")]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        group_ids = {g.id for g in payload.groups}
        assert "pes_confirmed" not in group_ids
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        assert len(rec_entries) == 1

    def test_pes_no_merge_same_candidate_id(self, tmp_path: Path):
        """Same candidate_id in both groups → separate entries, collision on rec side."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        cid = "ts_frame_005"
        recs = [_pes_recommendation(candidate_id=cid)]
        review_entries = [_pes_review_entry(candidate_id=cid, frame_index=5)]
        task = _make_task_dir(tmp_path,
                              pes_recommendations={"schema_version": "pes_recommendations_v1", "workflow": "PESsearch", "scan_dir": "WORK/07_PATH/pes_scan_001", "ts": recs, "intermediates": []},
                              pes_review={"schema_version": "pes_review_v1", "job_id": "j1", "status": "confirmed", "revision": 1, "selected": review_entries})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        rec_entries = [e for e in payload.entries if e.group_id == "pes_recommendations"]
        conf_entries = [e for e in payload.entries if e.group_id == "pes_confirmed"]
        assert len(rec_entries) == 1
        assert len(conf_entries) == 1
        assert rec_entries[0].id != conf_entries[0].id
        assert conf_entries[0].id == f"pes_{cid}"

    def test_pes_no_files_at_all(self, tmp_path: Path):
        """No PES files → empty payload + warning, no exception."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0


# ---------------------------------------------------------------------------
# Batch resolver tests
# ---------------------------------------------------------------------------


def _batch_product(*, item_id: str, tag: str = "INT", profile: str = "opt_freq",
                   path: str | None = None, label: str | None = None) -> dict:
    """Build a result_manifest product dict for a batch item."""
    p = path or f"structures/{item_id}__TAG_{tag}__optimized.xyz"
    return {
        "id": f"batch_{item_id}",
        "label": label if label is not None else f"{item_id} ({tag}, {profile})",
        "path": p,
        "kind": "structure",
    }


def _make_batch_task(
    tmp_path: Path,
    *,
    products: list[dict],
    trajectories: dict[str, dict] | None = None,
    workflow: str = "BatchOptimize",
) -> Path:
    """Create a task dir with result_manifest + optional optimization trajectories."""
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(exist_ok=True)
    manifest = {
        "version": 2,
        "task_id": "",
        "workflow": workflow,
        "status": "completed",
        "products": products,
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    for product in products:
        struct_path = result_dir / product["path"]
        struct_path.parent.mkdir(parents=True, exist_ok=True)
        if not struct_path.is_file():
            struct_path.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")
    if trajectories:
        for item_id, traj_payload in trajectories.items():
            traj_dir = tmp_path / "WORK" / "03_OPT" / "batch" / item_id / "optimize"
            traj_dir.mkdir(parents=True, exist_ok=True)
            (traj_dir / "optimization_trajectory.json").write_text(
                json.dumps(traj_payload), encoding="utf-8"
            )
            cycles_dir = traj_dir / "cycles"
            cycles_dir.mkdir(exist_ok=True)
            for cycle in traj_payload.get("cycles", []):
                geom_ref = cycle.get("geometry_ref", "")
                if geom_ref:
                    cycle_file = traj_dir / geom_ref
                    cycle_file.parent.mkdir(parents=True, exist_ok=True)
                    cycle_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")
    return tmp_path


def _optimization_trajectory(*, status: str = "completed", converged: bool = True,
                             cycles: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "status": status,
        "converged": converged,
        "cycles": cycles or [
            {"cycle": 1, "energy_hartree": -1.0, "geometry_ref": "cycles/cycle_0001.xyz",
             "rms_gradient": 1e-4, "max_gradient": 3e-4},
        ],
    }


class TestBatchResolver:
    """BatchOptimize resolver: per-item entries, roles, failure state, item_id filter."""

    def test_batch_two_items_completed(self, tmp_path: Path):
        """2 completed items -> 2 entries, both formal_result."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS"),
            _batch_product(item_id="item_002", tag="INT"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert len(payload.entries) == 2
        ids = [e.id for e in payload.entries]
        assert "batch_item_001" in ids
        assert "batch_item_002" in ids
        for entry in payload.entries:
            assert entry.source.kind == "formal_result"
            assert entry.status == "completed"

    def test_batch_role_from_tag_ts(self, tmp_path: Path):
        """TAG=TS -> role='ts'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="TS")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert payload.entries[0].role == "ts"

    def test_batch_role_from_tag_int(self, tmp_path: Path):
        """TAG=INT -> role='minimum'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert payload.entries[0].role == "minimum"

    def test_batch_badges_ts(self, tmp_path: Path):
        """TS item -> badge 'TS'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="TS")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert "TS" in payload.entries[0].badges

    def test_batch_failed_last_valid_cycle(self, tmp_path: Path):
        """Failed item with optimization trajectory -> source.kind='last_valid_cycle', badge."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(
            status="failed",
            cycles=[
                {"cycle": 1, "energy_hartree": -1.0, "geometry_ref": "cycles/cycle_0001.xyz",
                 "rms_gradient": 1e-4, "max_gradient": 3e-4},
                {"cycle": 2, "energy_hartree": -1.1, "geometry_ref": "cycles/cycle_0002.xyz",
                 "rms_gradient": 5e-5, "max_gradient": 1e-4},
            ],
        )
        task = _make_batch_task(
            tmp_path, products=[], trajectories={"item_001": traj}
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.source.kind == "last_valid_cycle"
        assert "failed-last-frame" in entry.badges
        assert "未收敛" in entry.label or "最后有效结构" in entry.label
        assert entry.source.geometry_ref == "WORK/03_OPT/batch/item_001/optimize/cycles/cycle_0002.xyz"

    def test_batch_failed_entry_not_labeled_optimized(self, tmp_path: Path):
        """Failed entry label does NOT say 'optimized'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(status="failed")
        task = _make_batch_task(
            tmp_path, products=[], trajectories={"item_001": traj}
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert "optimized" not in entry.label.lower()
        assert "optimized" not in (entry.source.geometry_ref or "").lower()

    def test_batch_item_id_filter_returns_one(self, tmp_path: Path):
        """item_id filter -> exactly one entry."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS"),
            _batch_product(item_id="item_002", tag="INT"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed",
            item_id="item_002",
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].id == "batch_item_002"

    def test_batch_unknown_item_id_raises(self, tmp_path: Path):
        """Unknown item_id -> StructureViewerError."""
        from acp.results.structure_viewer import (
            StructureViewerError,
            build_structure_viewer_payload,
        )

        products = [_batch_product(item_id="item_001")]
        task = _make_batch_task(tmp_path, products=products)
        with pytest.raises(StructureViewerError):
            build_structure_viewer_payload(
                task, job_id="j1", workflow="BatchOptimize", job_status="completed",
                item_id="nonexistent",
            )

    def test_batch_default_is_requested_item(self, tmp_path: Path):
        """item_id given -> default_entry_id = that item."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS"),
            _batch_product(item_id="item_002", tag="INT"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed",
            item_id="item_002",
        )
        assert payload.default_entry_id == "batch_item_002"

    def test_batch_default_first_completed(self, tmp_path: Path):
        """No item_id -> default = first completed item."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS"),
            _batch_product(item_id="item_002", tag="INT"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert payload.default_entry_id == "batch_item_001"

    def test_batch_no_items_warning(self, tmp_path: Path):
        """No batch products and no trajectories -> warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_batch_task(tmp_path, products=[])
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0

    def test_batch_geometry_endpoint(self, tmp_path: Path):
        """Geometry endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert "/api/v1/jobs/j1/structure-viewer/entries/batch_item_001/geometry" == entry.geometry.endpoint

    def test_batch_vibrations_available_false(self, tmp_path: Path):
        """TS item has vibrations.available=False (Wave 1)."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="TS")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert payload.entries[0].vibrations.available is False

    def test_batch_failed_empty_geometry_ref_no_crash(self, tmp_path: Path):
        """Last cycle with empty/missing geometry_ref -> entry built with geometry_ref=None, no crash."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(
            status="failed",
            cycles=[
                {"cycle": 1, "energy_hartree": -1.0, "geometry_ref": "cycles/cycle_0001.xyz",
                 "rms_gradient": 1e-4, "max_gradient": 3e-4},
                {"cycle": 2, "energy_hartree": -1.1, "geometry_ref": "",
                 "rms_gradient": 5e-5, "max_gradient": 1e-4},
            ],
        )
        task = _make_batch_task(
            tmp_path, products=[], trajectories={"item_001": traj}
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.source.kind == "last_valid_cycle"
        assert entry.source.geometry_ref is None


# ---------------------------------------------------------------------------
# Simple/scan resolver tests
# ---------------------------------------------------------------------------


def _make_simple_task(
    tmp_path: Path,
    *,
    workflow: str = "optimize",
    products: list[dict] | None = None,
    optimization_trajectory: dict | None = None,
    input_xyz: str | None = None,
) -> Path:
    """Create a task dir for simple workflow tests."""
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(exist_ok=True)

    manifest = {
        "version": 2,
        "task_id": "",
        "workflow": workflow,
        "status": "completed",
        "products": products or [],
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    for product in (products or []):
        if product.get("path"):
            product_path = result_dir / product["path"]
            product_path.parent.mkdir(parents=True, exist_ok=True)
            if not product_path.is_file():
                product_path.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    if optimization_trajectory is not None:
        opt_dir = tmp_path / "WORK" / "03_OPT"
        opt_dir.mkdir(parents=True, exist_ok=True)
        (opt_dir / "optimization_trajectory.json").write_text(
            json.dumps(optimization_trajectory), encoding="utf-8"
        )
        for cycle in optimization_trajectory.get("cycles", []):
            geom_ref = cycle.get("geometry_ref", "")
            if geom_ref:
                cycle_file = opt_dir / geom_ref
                cycle_file.parent.mkdir(parents=True, exist_ok=True)
                cycle_file.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    if input_xyz is not None:
        (tmp_path / "input.xyz").write_text(input_xyz, encoding="utf-8")

    return tmp_path


def _make_scan_task(
    tmp_path: Path,
    *,
    trajectory: dict,
    frame_files: dict[str, str] | None = None,
) -> Path:
    """Create a task dir for scan workflow tests."""
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")
    result_dir = tmp_path / "RESULT"
    result_dir.mkdir(exist_ok=True)

    traj_dir = result_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    (traj_dir / "scan_trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )

    manifest = {
        "version": 2,
        "task_id": "",
        "workflow": "scan",
        "status": "completed",
        "products": [
            {"id": "scan_trajectory", "label": "Relaxed scan trajectory",
             "path": "trajectories/scan_trajectory.json", "kind": "trajectory"},
        ],
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    if frame_files:
        for rel_path, content in frame_files.items():
            frame_path = result_dir / rel_path
            frame_path.parent.mkdir(parents=True, exist_ok=True)
            frame_path.write_text(content, encoding="utf-8")

    return tmp_path


class TestSimpleResolver:
    """Simple workflow resolver: optimize/xtb-optimize/singlepoint/frequency."""

    def test_optimize_completed_formal_product(self, tmp_path: Path):
        """Optimize with formal structure product -> formal_result default."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_optimize_structure", "label": "optimize (step 0) — structure",
             "path": "WORK/03_OPT/optimized.xyz", "kind": "structure"},
            {"id": "step_0_optimize_energy", "label": "optimize (step 0) — energy",
             "path": "", "kind": "energy_report"},
        ]
        task = _make_simple_task(tmp_path, workflow="optimize", products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "simple_optimize"
        assert entry.source.kind == "formal_result"
        assert entry.status == "completed"
        assert payload.default_entry_id == "simple_optimize"

    def test_optimize_failed_trajectory(self, tmp_path: Path):
        """Optimize with failed trajectory -> last_valid_cycle, badge, label."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(
            status="failed",
            converged=False,
            cycles=[
                {"cycle": 1, "energy_hartree": -1.0, "geometry_ref": "cycles/cycle_0001.xyz",
                 "rms_gradient": 1e-3, "max_gradient": 3e-3},
                {"cycle": 2, "energy_hartree": -1.1, "geometry_ref": "cycles/cycle_0002.xyz",
                 "rms_gradient": 5e-4, "max_gradient": 1e-3},
            ],
        )
        task = _make_simple_task(tmp_path, workflow="optimize", optimization_trajectory=traj)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="failed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "simple_optimize"
        assert entry.source.kind == "last_valid_cycle"
        assert entry.status == "failed"
        assert "failed-last-frame" in entry.badges
        assert "未收敛" in entry.label
        assert "最后有效结构" in entry.label
        assert entry.source.geometry_ref == "WORK/03_OPT/cycles/cycle_0002.xyz"

    def test_optimize_no_geometry_warning(self, tmp_path: Path):
        """Optimize with no geometry at all -> warnings + no entries, no crash."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_simple_task(tmp_path, workflow="optimize", products=[])
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0

    def test_singlepoint_input_structure(self, tmp_path: Path):
        """Singlepoint -> input structure + energy + '几何未改变' badge."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_singlepoint_energy", "label": "singlepoint (step 0) — energy",
             "path": "", "kind": "energy_report", "metadata": {"energy_hartree": -76.5}},
        ]
        input_xyz = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
        task = _make_simple_task(
            tmp_path, workflow="singlepoint", products=products, input_xyz=input_xyz
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="singlepoint", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "simple_singlepoint"
        assert entry.source.kind == "calculation_input"
        assert entry.source.geometry_ref == "input.xyz"
        assert "几何未改变" in entry.badges
        assert entry.energy.value == pytest.approx(-76.5)

    def test_frequency_input_structure(self, tmp_path: Path):
        """Frequency -> input structure."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        input_xyz = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
        task = _make_simple_task(
            tmp_path, workflow="frequency", products=[], input_xyz=input_xyz
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="frequency", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "simple_frequency"
        assert entry.source.kind == "calculation_input"
        assert entry.source.geometry_ref == "input.xyz"
        # F2 fix: frequency jobs only report available=True when actual
        # frequency data exists (product JSON or historical ORCA output).
        # This fixture has no data, so available=False is correct.
        assert entry.vibrations.available is False
        assert entry.vibrations.endpoint is None
        assert entry.vibrations.imaginary_count is None
        assert entry.vibrations.source is None

    def test_xtb_optimize_uses_optimize_resolver(self, tmp_path: Path):
        """xtb-optimize follows the same path as optimize."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_optimize_structure", "label": "optimize (step 0) — structure",
             "path": "WORK/03_OPT/optimized.xyz", "kind": "structure"},
        ]
        task = _make_simple_task(tmp_path, workflow="xtb-optimize", products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="xtb-optimize", job_status="completed"
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].source.kind == "formal_result"

    def test_priority_formal_over_trajectory(self, tmp_path: Path):
        """Formal product takes priority over optimization trajectory."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_optimize_structure", "label": "optimize (step 0) — structure",
             "path": "WORK/03_OPT/optimized.xyz", "kind": "structure"},
        ]
        traj = _optimization_trajectory(status="failed", converged=False)
        task = _make_simple_task(
            tmp_path, workflow="optimize", products=products, optimization_trajectory=traj
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].source.kind == "formal_result"

    def test_no_result_manifest_warning(self, tmp_path: Path):
        """No result manifest -> warning, no entries."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        (tmp_path / "job.json").write_text("{}")
        (tmp_path / "task.json").write_text("{}")
        payload = build_structure_viewer_payload(
            tmp_path, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0


class TestScanResolver:
    """Scan resolver: frame entries in file order, default = lowest energy."""

    def test_scan_four_frames(self, tmp_path: Path):
        """4-frame scan -> 4 entries in file order, default = lowest-energy frame."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        trajectory = {
            "workflow": "scan",
            "frame_count": 4,
            "successful_frame_count": 4,
            "frames": [
                {"index": 0, "path": "structures/scan_frame_000.xyz", "progress": 0.0, "energy_hartree": -1.0},
                {"index": 1, "path": "structures/scan_frame_001.xyz", "progress": 0.33, "energy_hartree": -1.2},
                {"index": 2, "path": "structures/scan_frame_002.xyz", "progress": 0.67, "energy_hartree": -1.5},
                {"index": 3, "path": "structures/scan_frame_003.xyz", "progress": 1.0, "energy_hartree": -1.1},
            ],
        }
        frame_files = {
            "structures/scan_frame_000.xyz": "1\ntest\nH 0 0 0\n",
            "structures/scan_frame_001.xyz": "1\ntest\nH 0 0 1\n",
            "structures/scan_frame_002.xyz": "1\ntest\nH 0 1 0\n",
            "structures/scan_frame_003.xyz": "1\ntest\nH 1 0 0\n",
        }
        task = _make_scan_task(tmp_path, trajectory=trajectory, frame_files=frame_files)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        assert len(payload.entries) == 4

        # Entries in file order
        ids = [e.id for e in payload.entries]
        assert ids == ["scan_frame_0", "scan_frame_1", "scan_frame_2", "scan_frame_3"]

        # Default = lowest energy (frame 2, energy -1.5)
        assert payload.default_entry_id == "scan_frame_2"

        # All entries have correct source
        for entry in payload.entries:
            assert entry.source.kind == "formal_result"
            assert entry.source.frame_index is not None

    def test_scan_default_lowest_energy(self, tmp_path: Path):
        """Default is the frame with lowest energy, not first or last."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        trajectory = {
            "workflow": "scan",
            "frame_count": 3,
            "frames": [
                {"index": 0, "path": "structures/scan_frame_000.xyz", "energy_hartree": -1.0},
                {"index": 1, "path": "structures/scan_frame_001.xyz", "energy_hartree": -2.0},
                {"index": 2, "path": "structures/scan_frame_002.xyz", "energy_hartree": -1.5},
            ],
        }
        task = _make_scan_task(tmp_path, trajectory=trajectory)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        assert payload.default_entry_id == "scan_frame_1"

    def test_scan_failed_frame_status(self, tmp_path: Path):
        """Frame with None energy -> status='failed'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        trajectory = {
            "workflow": "scan",
            "frame_count": 2,
            "frames": [
                {"index": 0, "path": "structures/scan_frame_000.xyz", "energy_hartree": -1.0},
                {"index": 1, "path": "structures/scan_frame_001.xyz", "energy_hartree": None},
            ],
        }
        task = _make_scan_task(tmp_path, trajectory=trajectory)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        assert len(payload.entries) == 2
        assert payload.entries[0].status == "completed"
        assert payload.entries[1].status == "failed"

    def test_scan_no_trajectory_warning(self, tmp_path: Path):
        """No scan trajectory -> warning, no entries."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        (tmp_path / "job.json").write_text("{}")
        (tmp_path / "task.json").write_text("{}")
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        manifest = {"version": 2, "task_id": "", "workflow": "scan", "status": "completed", "products": []}
        (result_dir / "result_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        payload = build_structure_viewer_payload(
            tmp_path, job_id="j1", workflow="scan", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0

    def test_scan_not_reordered_by_energy(self, tmp_path: Path):
        """Entries stay in file order even if energies are not sorted."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        trajectory = {
            "workflow": "scan",
            "frame_count": 3,
            "frames": [
                {"index": 0, "path": "structures/scan_frame_000.xyz", "energy_hartree": -3.0},
                {"index": 1, "path": "structures/scan_frame_001.xyz", "energy_hartree": -1.0},
                {"index": 2, "path": "structures/scan_frame_002.xyz", "energy_hartree": -2.0},
            ],
        }
        task = _make_scan_task(tmp_path, trajectory=trajectory)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        energies = [e.energy.value for e in payload.entries]
        assert energies == [-3.0, -1.0, -2.0]

    def test_scan_geometry_endpoint(self, tmp_path: Path):
        """Geometry endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        trajectory = {
            "workflow": "scan",
            "frame_count": 1,
            "frames": [
                {"index": 0, "path": "structures/scan_frame_000.xyz", "energy_hartree": -1.0},
            ],
        }
        task = _make_scan_task(tmp_path, trajectory=trajectory)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="scan", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.geometry.endpoint == "/api/v1/jobs/j1/structure-viewer/entries/scan_frame_0/geometry"


# ---------------------------------------------------------------------------
# Legacy resolver tests
# ---------------------------------------------------------------------------


def _make_legacy_task(
    tmp_path: Path,
    *,
    result_manifest: dict | None = None,
    result_summary: dict | None = None,
) -> Path:
    """Create a task dir for legacy resolver tests."""
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")

    if result_manifest is not None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps(result_manifest), encoding="utf-8"
        )
        # Create structure files referenced by products
        for product in result_manifest.get("products", []):
            if product.get("path") and product.get("kind") in ("structure", "xyz"):
                struct_path = result_dir / product["path"]
                struct_path.parent.mkdir(parents=True, exist_ok=True)
                if not struct_path.is_file():
                    struct_path.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    if result_summary is not None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "result_summary.json").write_text(
            json.dumps(result_summary), encoding="utf-8"
        )
        for product in result_summary.get("products", []):
            if product.get("path"):
                struct_path = result_dir / product["path"]
                struct_path.parent.mkdir(parents=True, exist_ok=True)
                if not struct_path.is_file():
                    struct_path.write_text("1\ntest\nH 0 0 0\n", encoding="utf-8")

    return tmp_path


class TestLegacyResolver:
    """Legacy fallback: result_manifest products then result_summary.json."""

    def test_legacy_result_manifest_products(self, tmp_path: Path):
        """result_manifest structure products → legacy entries with 兼容模式 badge."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "conf_0001", "label": "Conformer 0001", "path": "conformers/0001.xyz", "kind": "structure"},
                {"id": "conf_0002", "label": "Conformer 0002", "path": "conformers/0002.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        assert len(payload.entries) == 2
        for entry in payload.entries:
            assert entry.source.kind == "formal_result"
            assert "兼容模式" in entry.badges
            assert entry.id.startswith("legacy_")
            assert len(entry.id) == len("legacy_") + 12

    def test_legacy_entry_id_deterministic(self, tmp_path: Path):
        """Legacy entry ids are deterministic sha256(relpath)[:12]."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "p1", "label": "S", "path": "conformers/0001.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        expected_hash = hashlib.sha256("conformers/0001.xyz".encode("utf-8")).hexdigest()[:12]
        assert payload.entries[0].id == f"legacy_{expected_hash}"

    def test_legacy_result_summary_fallback(self, tmp_path: Path):
        """No result_manifest → fall back to result_summary.json products."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        summary = {
            "workflow": "energy",
            "status": "completed",
            "products": [
                {"path": "finalDFT/global_min.xyz", "role": "final_stable_structure", "kind": "xyz"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_summary=summary)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="energy", job_status="completed"
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].source.kind == "formal_result"
        assert "兼容模式" in payload.entries[0].badges

    def test_legacy_default_first_entry(self, tmp_path: Path):
        """default_entry_id = first legacy entry."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "p1", "label": "A", "path": "a.xyz", "kind": "structure"},
                {"id": "p2", "label": "B", "path": "b.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        assert payload.default_entry_id == payload.entries[0].id

    def test_legacy_no_products_warning(self, tmp_path: Path):
        """No products at all → empty + warning, no exception."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert len(payload.warnings) > 0

    def test_legacy_geometry_endpoint(self, tmp_path: Path):
        """Legacy entry geometry endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "p1", "label": "S", "path": "s.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        entry = payload.entries[0]
        assert "j1" in entry.geometry.endpoint
        assert entry.id in entry.geometry.endpoint

    def test_legacy_skips_non_structure_products(self, tmp_path: Path):
        """Non-structure kind products are skipped."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "report", "label": "Report", "path": "report.json", "kind": "report"},
                {"id": "p1", "label": "S", "path": "s.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        assert len(payload.entries) == 1


# ---------------------------------------------------------------------------
# IRC resolver tests
# ---------------------------------------------------------------------------


def _make_irc_task(
    tmp_path: Path,
    *,
    forward_xyz: str | None = None,
    reverse_xyz: str | None = None,
) -> Path:
    """Create a task dir with optional IRC endpoint files."""
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")
    result_dir = tmp_path / "RESULT"
    irc_dir = result_dir / "irc"
    irc_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "version": 2,
        "task_id": "",
        "workflow": "irc",
        "status": "completed",
        "products": [],
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )

    if forward_xyz is not None:
        (irc_dir / "irc_forward.xyz").write_text(forward_xyz, encoding="utf-8")
    if reverse_xyz is not None:
        (irc_dir / "irc_reverse.xyz").write_text(reverse_xyz, encoding="utf-8")

    return tmp_path


class TestIrcResolver:
    """IRC per-frame projection: one entry per frame, forward/reverse groups."""

    @staticmethod
    def _frames_xyz(comments: list[str]) -> str:
        blocks = []
        for i, comment in enumerate(comments):
            blocks.append(f"2\n{comment}\nC 0 0 {i}\nH 0 0 {i + 1}\n")
        return "".join(blocks)

    def test_irc_multiframe_six_entries(self, tmp_path: Path):
        """3+3 frames → 6 per-frame entries, two direction groups, forward_0 default."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        fwd = self._frames_xyz(["ts", "mid", "energy = -1.5"])
        rev = self._frames_xyz(["ts", "mid", "energy = -2.5"])
        task = _make_irc_task(tmp_path, forward_xyz=fwd, reverse_xyz=rev)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        assert [e.id for e in payload.entries] == [
            "irc_forward_0", "irc_forward_1", "irc_forward_2",
            "irc_reverse_0", "irc_reverse_1", "irc_reverse_2",
        ]
        group_ids = {g.id: g for g in payload.groups}
        assert set(group_ids) == {"irc_forward", "irc_reverse"}
        assert group_ids["irc_forward"].label == "正向"
        assert group_ids["irc_reverse"].label == "反向"
        assert payload.default_entry_id == "irc_forward_0"
        assert not any("awaiting_projection" in w.lower() for w in payload.warnings)

    def test_irc_entries_keep_file_order(self, tmp_path: Path):
        """Entries stay in file order (never reordered), frame_index per frame."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        fwd = self._frames_xyz(["e = 5.0", "e = 1.0", "e = 3.0"])  # non-monotonic
        rev = self._frames_xyz(["e = 4.0", "e = 0.5", "e = 2.0"])
        task = _make_irc_task(tmp_path, forward_xyz=fwd, reverse_xyz=rev)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        forward_frames = [e.source.frame_index for e in payload.entries if e.group_id == "irc_forward"]
        assert forward_frames == [0, 1, 2]
        assert payload.entries[0].label == "IRC 正向 1"

    def test_irc_no_files_no_placeholder_warning(self, tmp_path: Path):
        """No IRC files → groups present, no entries, no awaiting_projection."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_irc_task(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        assert {g.id for g in payload.groups} == {"irc_forward", "irc_reverse"}
        assert len(payload.entries) == 0
        assert not any("awaiting_projection" in w.lower() for w in payload.warnings)

    def test_irc_only_forward_missing_reverse_warning(self, tmp_path: Path):
        """Only forward file → 3 entries, default irc_forward_0, reverse-missing warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        fwd = self._frames_xyz(["ts", "mid", "end"])
        task = _make_irc_task(tmp_path, forward_xyz=fwd)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        assert [e.id for e in payload.entries] == ["irc_forward_0", "irc_forward_1", "irc_forward_2"]
        assert payload.default_entry_id == "irc_forward_0"
        assert any("reverse" in w and "missing" in w.lower() for w in payload.warnings)

    def test_irc_entry_source_fields(self, tmp_path: Path):
        """Per-frame source: frame_index matches the frame, ref points at the file."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        fwd = self._frames_xyz(["a", "b", "c"])
        task = _make_irc_task(tmp_path, forward_xyz=fwd)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        third = next(e for e in payload.entries if e.id == "irc_forward_2")
        assert third.source.kind == "formal_result"
        assert third.source.frame_index == 2
        assert third.source.geometry_ref is not None
        assert "irc_forward.xyz" in third.source.geometry_ref

    def test_irc_geometry_endpoint(self, tmp_path: Path):
        """IRC entry geometry endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        fwd = self._frames_xyz(["ts", "mid", "end"])
        task = _make_irc_task(tmp_path, forward_xyz=fwd)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="irc", job_status="completed"
        )
        entry = payload.entries[0]
        assert "j1" in entry.geometry.endpoint
        assert "irc_forward_0" in entry.geometry.endpoint


# ---------------------------------------------------------------------------
# Manual entry helper tests
# ---------------------------------------------------------------------------


class TestManualEntryHelper:
    """make_manual_entry: pure function, correct id/kind/vibrations."""

    def test_manual_entry_id_matches_helper(self):
        """make_manual_entry id matches manual_entry_id(relpath)."""
        from acp.results.structure_viewer import make_manual_entry, manual_entry_id

        entry = make_manual_entry(job_id="j1", relpath="some/file.xyz", label="Test")
        expected_id = manual_entry_id("some/file.xyz")
        assert entry.id == expected_id

    def test_manual_entry_source_kind(self):
        """source.kind = 'manual_file'."""
        from acp.results.structure_viewer import make_manual_entry

        entry = make_manual_entry(job_id="j1", relpath="f.xyz", label="F")
        assert entry.source.kind == "manual_file"

    def test_manual_entry_vibrations_unavailable(self):
        """vibrations.available = False always."""
        from acp.results.structure_viewer import make_manual_entry

        entry = make_manual_entry(job_id="j1", relpath="f.xyz", label="F")
        assert entry.vibrations.available is False

    def test_manual_entry_geometry_endpoint(self):
        """geometry.endpoint uses job_id and entry_id."""
        from acp.results.structure_viewer import make_manual_entry

        entry = make_manual_entry(job_id="j1", relpath="f.xyz", label="F")
        assert "j1" in entry.geometry.endpoint
        assert entry.id in entry.geometry.endpoint

    def test_manual_entry_label_propagated(self):
        """Label is propagated from argument."""
        from acp.results.structure_viewer import make_manual_entry

        entry = make_manual_entry(job_id="j1", relpath="f.xyz", label="My Molecule")
        assert entry.label == "My Molecule"

    def test_manual_entry_geometry_ref(self):
        """source.geometry_ref = relpath."""
        from acp.results.structure_viewer import make_manual_entry

        entry = make_manual_entry(job_id="j1", relpath="path/to/file.xyz", label="F")
        assert entry.source.geometry_ref == "path/to/file.xyz"


# ---------------------------------------------------------------------------
# Revision refresh tests
# ---------------------------------------------------------------------------


class TestRevisionRefresh:
    """Revision refresh: changes on manifest touch, stable on rebuild, immutability."""

    def test_revision_changes_on_confsearch_manifest_touch(self, tmp_path: Path):
        """Touch confsearch_manifest.json → different revision."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        cs = {"schema_version": "confsearch_v1", "workflow": "Confsearch", "conformers": []}
        task = _make_task_dir(tmp_path, confsearch_manifest=cs)

        p1 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        r1 = p1.revision

        cs["conformers"] = [{"id": "c1"}]
        (task / "RESULT" / "confsearch" / "confsearch_manifest.json").write_text(
            json.dumps(cs), encoding="utf-8"
        )

        p2 = build_structure_viewer_payload(task, job_id="j1", workflow="Confsearch", job_status="completed")
        r2 = p2.revision
        assert r1 != r2

    def test_revision_stable_on_rebuild(self, tmp_path: Path):
        """Same inputs → same revision across two calls."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        rm = {"version": 2, "task_id": "t", "workflow": "nmr", "status": "completed", "products": []}
        task = _make_task_dir(tmp_path, result_manifest=rm)

        p1 = build_structure_viewer_payload(task, job_id="j1", workflow="nmr", job_status="completed")
        p2 = build_structure_viewer_payload(task, job_id="j1", workflow="nmr", job_status="completed")
        assert p1.revision == p2.revision

    def test_payload_frozen_cannot_be_mutated(self, tmp_path: Path):
        """Frozen dataclass → cannot mutate fields after construction."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        with pytest.raises(AttributeError):
            payload.revision = "tampered"  # type: ignore[misc]

    def test_entries_tuple_frozen(self, tmp_path: Path):
        """entries is a tuple, not a list — cannot append."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert isinstance(payload.entries, tuple)

    def test_warnings_tuple_frozen(self, tmp_path: Path):
        """warnings is a tuple, not a list."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="optimize", job_status="completed"
        )
        assert isinstance(payload.warnings, tuple)


# ---------------------------------------------------------------------------
# Overlay + RMSD tests (todo 40)
# ---------------------------------------------------------------------------

_ETHANOL_LIKE = [
    ("C", 0.000, 0.000, 0.000),
    ("O", 1.430, 0.000, 0.000),
    ("H", -0.360, 1.030, 0.000),
    ("H", -0.360, -0.515, 0.890),
    ("H", -0.360, -0.515, -0.890),
    ("H", 1.740, 0.900, 0.000),
]


def _rotated(rows: list[tuple[str, float, float, float]], angle: float, shift: tuple[float, float, float]) -> list[tuple[str, float, float, float]]:
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out = []
    for sym, x, y, z in rows:
        xr = x * cos_a - y * sin_a + shift[0]
        yr = x * sin_a + y * cos_a + shift[1]
        out.append((sym, xr, yr, z + shift[2]))
    return out


def _xyz_text(rows: list[tuple[str, float, float, float]]) -> str:
    lines = [str(len(rows)), "overlay-fixture"]
    for sym, x, y, z in rows:
        lines.append(f"{sym} {x:.6f} {y:.6f} {z:.6f}")
    return "\n".join(lines) + "\n"


def _overlay_entry(entry_id: str, ref: str, frame_index: int | None = None):
    from acp.results.structure_viewer import (
        StructureViewerEntry,
        StructureViewerGeometry,
        StructureViewerSource,
        StructureViewerVibrations,
    )

    return StructureViewerEntry(
        id=entry_id,
        group_id="g",
        label=entry_id,
        role="minimum",
        status="completed",
        geometry=StructureViewerGeometry(endpoint="/x", format="xyz"),
        source=StructureViewerSource(
            kind="formal_result", frame_index=frame_index, geometry_ref=ref
        ),
        vibrations=StructureViewerVibrations(available=False),
    )


def _overlay_workdir(tmp_path: Path, files: dict[str, str]) -> Path:
    geo_dir = tmp_path / "RESULT" / "overlay"
    geo_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (geo_dir / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_overlay_rmsd(tmp_path: Path):
    """Acceptance: identity mapping on same-order geometries gives the exact
    Kabsch RMSD (< 1e-6 for a pure rotation+translation); permuted atom order
    resolves via unique MCS; unprovable pairs withhold the mapping."""
    from acp.results.structure_viewer import compute_overlay

    rotated_rows = _rotated(_ETHANOL_LIKE, 0.7, (5.0, -2.0, 0.5))
    work_dir = _overlay_workdir(
        tmp_path,
        {
            "a.xyz": _xyz_text(_ETHANOL_LIKE),
            "c.xyz": _xyz_text(rotated_rows),
        },
    )
    entry_a = _overlay_entry("a", "RESULT/overlay/a.xyz")
    entry_c = _overlay_entry("c", "RESULT/overlay/c.xyz")

    result = compute_overlay("job", work_dir, entry_a, entry_c)
    assert result["ok"] is True
    assert result["reason"] == "identity"
    assert result["mapping"] == [[i, i] for i in range(6)]
    assert result["n_mapped"] == 6
    assert result["rmsd"] is not None and result["rmsd"] < 1e-6
    assert result["max_displacement"]["distance"] < 1e-5
    assert set(result["max_displacement"]) == {"i", "j", "distance"}

    # Permuted atom order -> MCS path finds the unique mapping, RMSD ~ 0
    perm = [2, 0, 3, 1, 4, 5]
    permuted = [_ETHANOL_LIKE[i] for i in perm]
    permuted_rot = _rotated(permuted, -0.4, (1.0, 1.0, -0.3))
    work_dir2 = _overlay_workdir(
        tmp_path,
        {"p.xyz": _xyz_text(permuted_rot)},
    )
    entry_p = _overlay_entry("p", "RESULT/overlay/p.xyz")
    result_p = compute_overlay("job", work_dir2, entry_a, entry_p)
    assert result_p["ok"] is True
    assert result_p["reason"] == "mcs"
    assert result_p["mapping"] is not None
    assert len(result_p["mapping"]) == 6
    # inverse of the shuffle: a-atom i maps to its position in the perm file
    expected = {i: perm.index(i) for i in range(6)}
    for i, j in result_p["mapping"]:
        assert expected[i] == j
    assert result_p["rmsd"] is not None and result_p["rmsd"] < 1e-6

    # Unprovable: H2O vs ethane -> mapping withheld, ok stays true
    water = [("O", 0.0, 0.0, 0.0), ("H", 0.96, 0.0, 0.0), ("H", -0.24, 0.93, 0.0)]
    ethane = [
        ("C", -0.77, 0.0, 0.0), ("C", 0.77, 0.0, 0.0),
        ("H", -1.16, 1.02, 0.0), ("H", -1.16, -0.51, 0.88), ("H", -1.16, -0.51, -0.88),
        ("H", 1.16, 1.02, 0.0), ("H", 1.16, -0.51, 0.88), ("H", 1.16, -0.51, -0.88),
    ]
    work_dir3 = _overlay_workdir(
        tmp_path,
        {"w.xyz": _xyz_text(water), "e.xyz": _xyz_text(ethane)},
    )
    entry_w = _overlay_entry("w", "RESULT/overlay/w.xyz")
    entry_e = _overlay_entry("e", "RESULT/overlay/e.xyz")
    result_u = compute_overlay("job", work_dir3, entry_w, entry_e)
    assert result_u["ok"] is True
    assert result_u["reason"] == "unproven"
    assert result_u["mapping"] is None
    assert result_u["rmsd"] is None
    assert result_u["n_mapped"] == 0


def test_overlay_max_displacement_after_superposition(tmp_path: Path):
    """Perturb ONE atom -> that pair is the max-displacement pair."""
    from acp.results.structure_viewer import compute_overlay

    perturbed = list(_ETHANOL_LIKE)
    sym, x, y, z = perturbed[3]
    perturbed[3] = (sym, x + 0.30, y, z)
    work_dir = _overlay_workdir(
        tmp_path,
        {"a.xyz": _xyz_text(_ETHANOL_LIKE), "b.xyz": _xyz_text(perturbed)},
    )
    result = compute_overlay(
        "job",
        work_dir,
        _overlay_entry("a", "RESULT/overlay/a.xyz"),
        _overlay_entry("b", "RESULT/overlay/b.xyz"),
    )
    assert result["reason"] == "identity"
    assert result["max_displacement"]["i"] == 3
    assert result["max_displacement"]["j"] == 3
    # Kabsch redistributes part of the single-atom perturbation into the
    # optimal rotation/translation, so the post-superposition distance is
    # below the raw 0.30 shift but remains the largest mapped-pair distance.
    assert 0.15 < result["max_displacement"]["distance"] < 0.31
    assert result["rmsd"] <= result["max_displacement"]["distance"]


def test_overlay_frame_index_extraction(tmp_path: Path):
    """Multi-frame geometry_ref + frame_index reads the right block."""
    from acp.results.structure_viewer import compute_overlay

    frames = _xyz_text(_ETHANOL_LIKE) + _xyz_text(_rotated(_ETHANOL_LIKE, 0.9, (2, 2, 2)))
    work_dir = _overlay_workdir(tmp_path, {"traj.xyz": frames})
    result = compute_overlay(
        "job",
        work_dir,
        _overlay_entry("f0", "RESULT/overlay/traj.xyz", frame_index=0),
        _overlay_entry("f1", "RESULT/overlay/traj.xyz", frame_index=1),
    )
    assert result["reason"] == "identity"
    assert result["rmsd"] is not None and result["rmsd"] < 1e-6


def test_overlay_geometry_unreadable(tmp_path: Path):
    """Missing geometry files -> ok=False + geometry_unreadable."""
    from acp.results.structure_viewer import compute_overlay

    work_dir = _overlay_workdir(tmp_path, {})
    result = compute_overlay(
        "job",
        work_dir,
        _overlay_entry("a", "RESULT/overlay/nope.xyz"),
        _overlay_entry("b", "RESULT/overlay/nope2.xyz"),
    )
    assert result["ok"] is False
    assert result["reason"] == "geometry_unreadable"
    assert result["mapping"] is None


def test_overlay_path_escape_rejected(tmp_path: Path):
    """geometry_ref may not escape the work dir."""
    from acp.results.structure_viewer import compute_overlay

    work_dir = _overlay_workdir(tmp_path, {"a.xyz": _xyz_text(_ETHANOL_LIKE)})
    result = compute_overlay(
        "job",
        work_dir,
        _overlay_entry("a", "RESULT/overlay/a.xyz"),
        _overlay_entry("evil", "../../etc/passwd"),
    )
    assert result["ok"] is False
    assert result["reason"] == "geometry_unreadable"


# ---------------------------------------------------------------------------
# Cross-workflow acceptance matrix (todo 43, doc §11 backend rows)
# ---------------------------------------------------------------------------


class TestAcceptanceMatrix:
    """Doc §11 backend rows: status matrix, full entry orders, Boltzmann
    degradation, all-failed batch, resolver-level read-only."""

    _STATUSES = ("completed", "failed", "cancelled", "running")

    @staticmethod
    def _subdir(tmp_path: Path, name: str) -> Path:
        sub = tmp_path / name
        sub.mkdir(parents=True, exist_ok=True)
        return sub

    @staticmethod
    def _confsearch_task(tmp_path: Path) -> Path:
        conformers = [
            _conf(conf_id="0002", rank=2, energy=-99.9, weight=0.3),
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
            _conf(conf_id="0003", rank=3, energy=-99.8, weight=0.2),
        ]
        return _make_task_dir(
            tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers)
        )

    @staticmethod
    def _pes_task(tmp_path: Path) -> Path:
        recs_ts = [_pes_recommendation(candidate_id="ts_frame_005", kind="ts",
                                       confidence="high", frame_index=5)]
        recs_int = [_pes_recommendation(candidate_id="int_frame_002", kind="intermediate",
                                        confidence="medium", frame_index=2)]
        review = [
            _pes_review_entry(candidate_id="ts_frame_005", frame_index=5, role="TS"),
            _pes_review_entry(candidate_id="int_frame_009", frame_index=9, role="INT"),
        ]
        return _make_task_dir(
            tmp_path,
            pes_recommendations={
                "schema_version": "pes_recommendations_v1", "workflow": "PESsearch",
                "scan_dir": "WORK/07_PATH/pes_scan_001",
                "ts": recs_ts, "intermediates": recs_int,
            },
            pes_review={
                "schema_version": "pes_review_v1", "job_id": "j1",
                "status": "confirmed", "revision": 1, "selected": review,
            },
        )

    @staticmethod
    def _batch_task(tmp_path: Path) -> Path:
        products = [
            _batch_product(item_id="item_002", tag="INT"),
            _batch_product(item_id="item_001", tag="TS"),
        ]
        traj = _optimization_trajectory(
            status="failed", converged=False,
            cycles=[{"cycle": 1, "energy_hartree": -1.0,
                     "geometry_ref": "cycles/cycle_0001.xyz"}],
        )
        return _make_batch_task(tmp_path, products=products, trajectories={"item_003": traj})

    def test_status_matrix_payloads_build_all_workflows(self, tmp_path: Path):
        """completed/failed/cancelled/running: payload builds for every
        workflow; entry ids + default are status-stable; revision varies."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        builders = {
            "Confsearch": self._confsearch_task,
            "PESsearch": self._pes_task,
            "BatchOptimize": self._batch_task,
        }
        for workflow, builder in builders.items():
            task = builder(self._subdir(tmp_path, workflow.lower()))
            by_status = {}
            for status in self._STATUSES:
                payload = build_structure_viewer_payload(
                    task, job_id="j1", workflow=workflow, job_status=status
                )
                assert payload.entries, f"{workflow}/{status}: no entries"
                assert payload.job_status == status
                by_status[status] = payload
            ids_baseline = [e.id for e in by_status["completed"].entries]
            default_baseline = by_status["completed"].default_entry_id
            for status in self._STATUSES:
                assert [e.id for e in by_status[status].entries] == ids_baseline
                assert by_status[status].default_entry_id == default_baseline
            revisions = {p.revision for p in by_status.values()}
            assert len(revisions) >= 2, f"{workflow}: revision must vary with status"

    def test_confsearch_full_order_and_default_per_status(self, tmp_path: Path):
        """Manifest order (fixture deliberately non-sorted) + rank-1 default
        hold for failed/cancelled/running exactly as for completed."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = self._confsearch_task(tmp_path)
        for status in self._STATUSES:
            payload = build_structure_viewer_payload(
                task, job_id="j1", workflow="Confsearch", job_status=status
            )
            assert [e.id for e in payload.entries] == [
                "conf_0002", "conf_0001", "conf_0003",
            ]
            assert payload.default_entry_id == "conf_0001"

    def test_pes_full_entry_order_confirmed_first(self, tmp_path: Path):
        """Confirmed group leads (review order), then recommendations
        (ts then intermediates); recs stay algorithm_recommendation with
        confirmed=False and never become formal products."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = self._pes_task(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert [g.id for g in payload.groups] == ["pes_confirmed", "pes_recommendations"]
        assert [e.id for e in payload.entries] == [
            "pes_ts_frame_005", "pes_int_frame_009",  # confirmed (review order)
            "pes_ts_frame_005_d783f0",                # collision-resolved duplicate rec
            "pes_int_frame_002",
        ]
        assert payload.default_entry_id == "pes_ts_frame_005"
        for entry in payload.entries:
            if entry.group_id == "pes_recommendations":
                assert entry.source.kind == "algorithm_recommendation"
                assert entry.source.confirmed is False
            else:
                assert entry.source.kind == "manual_review"
                assert entry.source.confirmed is True

    def test_batch_completed_manifest_order_then_failed_appended(self, tmp_path: Path):
        """Completed items keep MANIFEST (product-list) order; failed items
        are appended after them; cancelled behaves identically."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = self._batch_task(tmp_path)
        expected_ids = ["batch_item_002", "batch_item_001", "batch_item_003"]
        expected_kinds = ["formal_result", "formal_result", "last_valid_cycle"]
        for status in ("completed", "cancelled"):
            payload = build_structure_viewer_payload(
                task, job_id="j1", workflow="BatchOptimize", job_status=status
            )
            assert [e.id for e in payload.entries] == expected_ids
            assert [e.source.kind for e in payload.entries] == expected_kinds
            assert payload.default_entry_id == "batch_item_002"

    def test_batch_all_items_failed_default_none(self, tmp_path: Path):
        """All-items-failed batch: every entry is last_valid_cycle and the
        resolver yields default None — PINNED observed behavior (the resolver
        defines no fallback default when no completed item exists; the
        frontend simply renders the first entry). Do not invent a default."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(
            status="failed", converged=False,
            cycles=[{"cycle": 1, "energy_hartree": -1.0,
                     "geometry_ref": "cycles/cycle_0001.xyz"}],
        )
        task = _make_batch_task(
            tmp_path, products=[], trajectories={"item_002": traj, "item_001": traj}
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="failed"
        )
        assert [e.id for e in payload.entries] == ["batch_item_001", "batch_item_002"]
        assert all(e.source.kind == "last_valid_cycle" for e in payload.entries)
        assert payload.default_entry_id is None

    def test_boltzmann_mixed_weights_fill_only_missing(self, tmp_path: Path):
        """Mixed weights: manifest-present values kept VERBATIM (no
        renormalization — sum may differ from 1 by design), only None
        entries filled with computed values, warning present; all-computed
        case sums to 1 within 1e-6."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.5),
            _conf(conf_id="0002", rank=2, energy=-99.9, weight=None),
            _conf(conf_id="0003", rank=3, energy=-99.8, weight=0.3),
        ]
        task = _make_task_dir(
            tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers)
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        weights = [e.boltzmann_weight for e in payload.entries]
        assert weights[0] == 0.5
        assert weights[2] == 0.3
        assert weights[1] is not None and 0.0 < weights[1] <= 1.0
        assert any("Boltzmann weights missing" in w for w in payload.warnings)

        all_missing = [
            _conf(conf_id=f"000{i}", rank=i, energy=-100.0 + 0.1 * (i - 1), weight=None)
            for i in (1, 2, 3)
        ]
        task2 = _make_task_dir(
            self._subdir(tmp_path, "b"),
            confsearch_manifest=_confsearch_manifest(conformers=all_missing),
        )
        payload2 = build_structure_viewer_payload(
            task2, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert abs(sum(e.boltzmann_weight for e in payload2.entries) - 1.0) < 1e-6

    def test_resolver_level_no_disk_writes(self, tmp_path: Path):
        """Resolver-level historical read-only: building payloads never
        mutates the task tree (API-level snapshot exists; this is the
        resolver equivalent across three workflows)."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        self._confsearch_task(self._subdir(tmp_path, "cs"))
        self._pes_task(self._subdir(tmp_path, "pes"))
        _ = _make_task_dir(
            self._subdir(tmp_path, "legacy"),
            result_manifest={
                "version": 2, "task_id": "", "workflow": "ensemble",
                "status": "completed",
                "products": [{"id": "p1", "label": "s", "path": "structures/a.xyz",
                              "kind": "xyz"}],
            },
        )
        snapshot = {
            p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(tmp_path.rglob("*")) if p.is_file()
        }
        assert len(snapshot) >= 5

        for workflow, root in (
            ("Confsearch", self._subdir(tmp_path, "cs")),
            ("PESsearch", self._subdir(tmp_path, "pes")),
            ("ensemble", self._subdir(tmp_path, "legacy")),
        ):
            payload = build_structure_viewer_payload(
                root, job_id="j1", workflow=workflow, job_status="completed"
            )
            assert payload.schema_version == "structure_viewer_v1"

        after = {
            p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in sorted(tmp_path.rglob("*")) if p.is_file()
        }
        assert snapshot == after


# ---------------------------------------------------------------------------
# F2 post-review fixes: catalog vibrations wiring + Boltzmann alignment
# ---------------------------------------------------------------------------


class TestF2CatalogVibrations:
    """F2 MAJOR-1: the catalog flag follows the per-item frequency product,
    making the Wave-5 vibration UI reachable end-to-end."""

    def test_batch_item_with_normal_modes_product_available_true(self, tmp_path: Path):
        """Completed batch item WITH {item}__normal_modes.json -> catalog
        available=True + vibrations endpoint set."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="TS")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        (freq_dir / "item_001__normal_modes.json").write_text(
            json.dumps({"schema_version": "normal_modes_v1", "atom_count": 1, "modes": []}),
            encoding="utf-8",
        )

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.id == "batch_item_001"
        assert entry.vibrations.available is True
        assert entry.vibrations.endpoint == (
            "/api/v1/jobs/j1/structure-viewer/entries/batch_item_001/vibrations"
        )

    def test_batch_mixed_items_partial_frequency(self, tmp_path: Path):
        """One item with the product, one without -> availability differs per
        entry (partial-frequency case at catalog level)."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS"),
            _batch_product(item_id="item_002", tag="INT"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        (freq_dir / "item_001__normal_modes.json").write_text(
            json.dumps({"schema_version": "normal_modes_v1", "atom_count": 1, "modes": []}),
            encoding="utf-8",
        )

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        by_id = {e.id: e for e in payload.entries}
        assert by_id["batch_item_001"].vibrations.available is True
        assert by_id["batch_item_002"].vibrations.available is False

    def test_batch_failed_item_stays_unavailable(self, tmp_path: Path):
        """Failed-trajectory items have no frequency product by design."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        traj = _optimization_trajectory(
            status="failed", converged=False,
            cycles=[{"cycle": 1, "energy_hartree": -1.0,
                     "geometry_ref": "cycles/cycle_0001.xyz"}],
        )
        task = _make_batch_task(tmp_path, products=[], trajectories={"item_001": traj})
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="failed"
        )
        assert payload.entries[0].vibrations.available is False

    def test_batch_historical_only_available_true(self, tmp_path: Path):
        """Batch item with ONLY WORK/04_FREQ/*.out (no product JSON) →
        catalog available=True, imaginary_count, source=historical_projection."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "WORK" / "04_FREQ" / "batch" / "item_001" / "frequency"
        freq_dir.mkdir(parents=True, exist_ok=True)
        fixture = Path(__file__).resolve().parent / "fixtures" / "structure_viewer" / "orca_freq_modes_full.txt"
        (freq_dir / "orca_freq.out").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.id == "batch_item_001"
        assert entry.vibrations.available is True
        assert entry.vibrations.source == "historical_projection"
        assert entry.vibrations.imaginary_count == 2
        assert entry.vibrations.endpoint.endswith("/vibrations")

    def test_batch_product_json_imaginary_count(self, tmp_path: Path):
        """Product JSON with 1 imaginary mode → imaginary_count=1, source=product."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="TS")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        modes_data = {
            "schema_version": "normal_modes_v1",
            "atom_count": 3,
            "geometry_product_id": None,
            "modes": [
                {"mode_index": 6, "frequency_cm1": -700.0, "imaginary": True, "vectors": [[0.0]*3]*3},
                {"mode_index": 7, "frequency_cm1": 500.0, "imaginary": False, "vectors": [[0.0]*3]*3},
            ],
        }
        (freq_dir / "item_001__normal_modes.json").write_text(json.dumps(modes_data), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is True
        assert entry.vibrations.source == "product"
        assert entry.vibrations.imaginary_count == 1

    def test_simple_frequency_historical_only(self, tmp_path: Path):
        """Simple frequency workflow with only WORK/04_FREQ → available=True, source=historical_projection."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        input_xyz = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
        task = _make_simple_task(tmp_path, workflow="frequency", products=[], input_xyz=input_xyz)
        freq_dir = task / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True, exist_ok=True)
        fixture = Path(__file__).resolve().parent / "fixtures" / "structure_viewer" / "orca_freq_modes_full.txt"
        (freq_dir / "orca_freq.out").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="frequency", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is True
        assert entry.vibrations.source == "historical_projection"
        assert entry.vibrations.imaginary_count == 2
        assert entry.vibrations.endpoint.endswith("/vibrations")

    def test_no_data_vibrations_unavailable(self, tmp_path: Path):
        """No frequency data at all → available=False, imaginary_count=None, source=None."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is False
        assert entry.vibrations.imaginary_count is None
        assert entry.vibrations.source is None

    def test_invalid_product_json_no_historical_unavailable(self, tmp_path: Path):
        """Malformed product JSON + no historical ORCA → catalog available=False."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        (freq_dir / "item_001__normal_modes.json").write_text("NOT VALID JSON {{{", encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is False
        assert entry.vibrations.imaginary_count is None
        assert entry.vibrations.source is None

    def test_invalid_product_json_falls_back_to_historical(self, tmp_path: Path):
        """Invalid product JSON + valid historical ORCA → catalog available=True,
        source=historical_projection (fallback to tier 3)."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        (freq_dir / "item_001__normal_modes.json").write_text("NOT VALID JSON {{{", encoding="utf-8")
        orca_dir = task / "WORK" / "04_FREQ" / "batch" / "item_001" / "frequency"
        orca_dir.mkdir(parents=True, exist_ok=True)
        fixture = Path(__file__).resolve().parent / "fixtures" / "structure_viewer" / "orca_freq_modes_full.txt"
        (orca_dir / "orca_freq.out").write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is True
        assert entry.vibrations.source == "historical_projection"
        assert entry.vibrations.imaginary_count == 2

    def test_wrong_schema_version_product_unavailable(self, tmp_path: Path):
        """Product JSON with wrong schema_version → treated as invalid, available=False."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [_batch_product(item_id="item_001", tag="INT")]
        task = _make_batch_task(tmp_path, products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        bad_data = {"schema_version": "wrong_version", "modes": []}
        (freq_dir / "item_001__normal_modes.json").write_text(json.dumps(bad_data), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is False

    def test_priority1_frequency_with_valid_product(self, tmp_path: Path):
        """Priority-1 (formal result) + frequency workflow + valid product →
        catalog available=True, source=product."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_frequency_structure", "label": "frequency (step 0) — structure",
             "path": "WORK/04_FREQ/optimized.xyz", "kind": "structure"},
        ]
        task = _make_simple_task(tmp_path, workflow="frequency", products=products)
        freq_dir = task / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        modes_data = {
            "schema_version": "normal_modes_v1",
            "atom_count": 3,
            "geometry_product_id": None,
            "modes": [
                {"mode_index": 6, "frequency_cm1": -700.0, "imaginary": True, "vectors": [[0.0]*3]*3},
            ],
        }
        (freq_dir / "normal_modes.json").write_text(json.dumps(modes_data), encoding="utf-8")

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="frequency", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.vibrations.available is True
        assert entry.vibrations.source == "product"
        assert entry.vibrations.imaginary_count == 1


class TestF2BoltzmannAlignment:
    """F2 MAJOR-2: malformed manifest rows must never shift weights."""

    def test_malformed_row_skipped_weights_aligned(self, tmp_path: Path):
        """[non-dict, rank1-low-energy, rank2-high-energy] -> 2 entries; the
        rank-1 conformer gets the LARGER weight; weights sum to 1 within
        1e-6; malformed-skip warning present."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers: list[object] = [
            "not-a-dict",
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=None),
            _conf(conf_id="0002", rank=2, energy=-99.9, weight=None),
        ]
        # a real writer corruption leaves the malformed row in place
        manifest = _confsearch_manifest(
            conformers=[c for c in conformers if isinstance(c, dict)]  # type: ignore[misc]
        )
        manifest["conformers"] = conformers  # type: ignore[assignment]
        task = _make_task_dir(tmp_path, confsearch_manifest=manifest)

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        assert [e.id for e in payload.entries] == ["conf_0001", "conf_0002"]
        assert any("Skipped 1 malformed" in w for w in payload.warnings)
        w1 = payload.entries[0].boltzmann_weight
        w2 = payload.entries[1].boltzmann_weight
        assert w1 is not None and w2 is not None
        assert w1 > w2, "rank-1 (lower energy) must carry the larger weight"
        assert abs((w1 + w2) - 1.0) < 1e-6

    def test_all_present_weights_still_sum_to_one(self, tmp_path: Path):
        """Regression: the un-corrupted path is byte-identical (sum 1e-6)."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        conformers = [
            _conf(conf_id="0001", rank=1, energy=-100.0, weight=0.7),
            _conf(conf_id="0002", rank=2, energy=-99.9, weight=0.3),
        ]
        task = _make_task_dir(
            tmp_path, confsearch_manifest=_confsearch_manifest(conformers=conformers)
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="Confsearch", job_status="completed"
        )
        weights = [e.boltzmann_weight for e in payload.entries]
        assert weights == [0.7, 0.3]
        assert abs(sum(weights) - 1.0) < 1e-6
        assert not any("malformed" in w for w in payload.warnings)


# ---------------------------------------------------------------------------
# Display-label normalization: formal results must never read as "input"
# ---------------------------------------------------------------------------


class TestDisplayLabelNormalization:
    """Formal-result labels carrying the legacy ``input`` prefix (BatchOptimize
    CLI items) are rewritten at projection time; meaningful labels and
    calculation-input structures stay untouched."""

    def test_batch_formal_result_label_not_input(self, tmp_path: Path):
        """'input (TS, opt_freq)' -> 'TS 优化结果'; INT -> '优化结果'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS", label="input (TS, opt_freq)"),
            _batch_product(item_id="item_002", tag="INT", label="input (INT, opt_freq)"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        by_id = {e.id: e for e in payload.entries}
        ts_label = by_id["batch_item_001"].label
        int_label = by_id["batch_item_002"].label
        assert not ts_label.lower().startswith("input")
        assert not int_label.lower().startswith("input")
        assert "优化" in ts_label or "TS" in ts_label
        assert ts_label == "TS 优化结果"
        assert int_label == "优化结果"

    def test_batch_formal_result_label_preserved_when_meaningful(self, tmp_path: Path):
        """A meaningful item name ('mol_A ...') passes through unchanged."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            _batch_product(item_id="item_001", tag="TS", label="mol_A (TS, opt_freq)"),
        ]
        task = _make_batch_task(tmp_path, products=products)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="BatchOptimize", job_status="completed"
        )
        assert payload.entries[0].label == "mol_A (TS, opt_freq)"

    def test_legacy_formal_result_label_normalized(self, tmp_path: Path):
        """Legacy manifest product 'input (something)' -> generic '计算结果'."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        manifest = {
            "version": 2,
            "task_id": "",
            "workflow": "nmr",
            "status": "completed",
            "products": [
                {"id": "p1", "label": "input (something)",
                 "path": "conformers/0001.xyz", "kind": "structure"},
            ],
        }
        task = _make_legacy_task(tmp_path, result_manifest=manifest)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="nmr", job_status="completed"
        )
        label = payload.entries[0].label
        assert not label.lower().startswith("input")
        assert label == "计算结果"

    def test_calculation_input_label_not_normalized(self, tmp_path: Path):
        """calculation_input entries keep their label — only formal results are
        rewritten, and the simple-resolver input fallback is untouched."""
        from acp.results.structure_viewer import (
            _normalize_display_label,
            build_structure_viewer_payload,
        )

        assert (
            _normalize_display_label("input.xyz", "calculation_input", "singlepoint")
            == "input.xyz"
        )

        input_xyz = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
        task = _make_simple_task(
            tmp_path, workflow="singlepoint", products=[], input_xyz=input_xyz
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="singlepoint", job_status="completed"
        )
        entry = payload.entries[0]
        assert entry.source.kind == "calculation_input"
        assert entry.label == "单点能"

    def test_default_entry_prefers_formal_result(self, tmp_path: Path):
        """With a formal structure product AND input.xyz both available, the
        formal result wins priority and is the default selection."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        products = [
            {"id": "step_0_singlepoint_structure", "label": "input (singlepoint)",
             "path": "WORK/03_SP/optimized.xyz", "kind": "structure"},
            {"id": "step_0_singlepoint_energy", "label": "singlepoint (step 0) — energy",
             "path": "", "kind": "energy_report", "metadata": {"energy_hartree": -76.5}},
        ]
        input_xyz = "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"
        task = _make_simple_task(
            tmp_path, workflow="singlepoint", products=products, input_xyz=input_xyz
        )
        assert (task / "input.xyz").is_file(), "calculation-input source must exist"

        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="singlepoint", job_status="completed"
        )
        formal_ids = [e.id for e in payload.entries if e.source.kind == "formal_result"]
        assert formal_ids, "formal result must be present when a structure product exists"
        assert payload.default_entry_id == formal_ids[0]
