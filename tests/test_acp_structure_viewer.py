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

    def test_unimplemented_resolver_warns_for_pessearch(self, tmp_path: Path):
        """Resolvers not yet implemented produce a specific warning."""
        from acp.results.structure_viewer import build_structure_viewer_payload

        task = _make_task_dir(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="PESsearch", job_status="completed"
        )
        assert any("not yet implemented" in w.lower() for w in payload.warnings)


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
