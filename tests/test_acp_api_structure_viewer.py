"""Tests for structure-viewer API schemas (todo 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from acp.api.v1_schemas import (
    StructureAssetCreateRequest,
    StructureAssetModel,
    StructureViewerEntryModel,
    StructureViewerGroupModel,
    StructureViewerModeModel,
    StructureViewerPayloadModel,
    StructureViewerVibrationsResponse,
)


# ── Fixture helpers (inline; do NOT import from test_acp_structure_viewer) ──


def _make_entry_dict(
    entry_id: str = "conf_0001",
    group_id: str = "final_conformers",
    label: str = "构象 0001",
    role: str = "minimum",
    status: str = "completed",
    *,
    energy_value: float | None = -100.0,
    energy_unit: str = "hartree",
    energy_kind: str = "electronic",
    temperature_k: float | None = None,
    relative_energy_kcal: float | None = 0.0,
    boltzmann_weight: float | None = 0.5,
    source_kind: str = "formal_result",
    confirmed: bool | None = None,
    badges: list[str] | None = None,
    vib_available: bool = False,
) -> dict:
    """Build a single entry dict matching StructureViewerEntry.to_dict() shape."""
    d: dict = {
        "id": entry_id,
        "group_id": group_id,
        "label": label,
        "role": role,
        "status": status,
        "geometry": {"endpoint": f"/api/v1/jobs/j1/structure-viewer/entries/{entry_id}/geometry", "format": "xyz"},
        "energy": {"value": energy_value, "unit": energy_unit, "kind": energy_kind},
        "relative_energy_kcal": relative_energy_kcal,
        "boltzmann_weight": boltzmann_weight,
        "source": {"kind": source_kind},
        "badges": badges if badges is not None else [],
        "vibrations": {"available": vib_available},
    }
    if temperature_k is not None:
        d["energy"]["temperature_k"] = temperature_k
    if confirmed is not None:
        d["source"]["confirmed"] = confirmed
    return d


def _make_group_dict(group_id: str = "final_conformers", label: str = "最终构象", kind: str = "ensemble") -> dict:
    return {"id": group_id, "label": label, "kind": kind}


def _make_payload_dict(
    *,
    schema_version: str = "structure_viewer_v1",
    job_id: str = "j1",
    workflow: str = "Confsearch",
    job_status: str = "completed",
    revision: str = "abcd1234efgh5678",
    default_entry_id: str | None = "conf_0001",
    groups: list[dict] | None = None,
    entries: list[dict] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    return {
        "schema_version": schema_version,
        "job_id": job_id,
        "workflow": workflow,
        "job_status": job_status,
        "revision": revision,
        "default_entry_id": default_entry_id,
        "groups": groups if groups is not None else [_make_group_dict()],
        "entries": entries if entries is not None else [_make_entry_dict()],
        "warnings": warnings if warnings is not None else [],
    }


# ── StructureViewerPayloadModel ─────────────────────────────────────────────


class TestStructureViewerPayloadModel:
    """Round-trip validation of the full payload model."""

    def test_round_trip_minimal(self):
        """Minimal payload dict → model → dict preserves key fields."""
        d = _make_payload_dict()
        model = StructureViewerPayloadModel.model_validate(d)
        assert model.schema_version == "structure_viewer_v1"
        assert model.job_id == "j1"
        assert model.workflow == "Confsearch"
        assert model.job_status == "completed"
        assert model.revision == "abcd1234efgh5678"
        assert model.default_entry_id == "conf_0001"
        assert model.availability == "ready"
        assert len(model.groups) == 1
        assert len(model.entries) == 1
        assert model.warnings == []

    def test_round_trip_preserves_entry_fields(self):
        """Entry sub-model round-trips all fields."""
        entry = _make_entry_dict(
            energy_value=-50.0,
            energy_kind="gibbs",
            temperature_k=298.15,
            relative_energy_kcal=1.23,
            boltzmann_weight=0.7,
            source_kind="manual_review",
            confirmed=True,
            badges=["rank-1", "selected"],
            vib_available=True,
        )
        d = _make_payload_dict(entries=[entry])
        model = StructureViewerPayloadModel.model_validate(d)
        e = model.entries[0]
        assert e.id == "conf_0001"
        assert e.energy.value == -50.0
        assert e.energy.kind == "gibbs"
        assert e.energy.temperature_k == 298.15
        assert e.relative_energy_kcal == 1.23
        assert e.boltzmann_weight == 0.7
        assert e.source.kind == "manual_review"
        assert e.source.confirmed is True
        assert e.badges == ["rank-1", "selected"]
        assert e.vibrations.available is True

    def test_availability_literal_accepts_valid(self):
        """availability accepts 'ready', 'pending_fetch', 'unavailable'."""
        for val in ("ready", "pending_fetch", "unavailable"):
            d = _make_payload_dict()
            d["availability"] = val
            model = StructureViewerPayloadModel.model_validate(d)
            assert model.availability == val

    def test_availability_literal_rejects_invalid(self):
        """availability rejects values outside the Literal set."""
        d = _make_payload_dict()
        d["availability"] = "unknown_status"
        with pytest.raises(ValidationError):
            StructureViewerPayloadModel.model_validate(d)

    def test_missing_schema_version_raises(self):
        """Missing required schema_version → ValidationError."""
        d = _make_payload_dict()
        del d["schema_version"]
        with pytest.raises(ValidationError):
            StructureViewerPayloadModel.model_validate(d)

    def test_default_availability_is_ready(self):
        """availability defaults to 'ready' when omitted."""
        d = _make_payload_dict()
        model = StructureViewerPayloadModel.model_validate(d)
        assert model.availability == "ready"

    def test_default_entry_id_can_be_none(self):
        """default_entry_id=None is valid."""
        d = _make_payload_dict(default_entry_id=None)
        model = StructureViewerPayloadModel.model_validate(d)
        assert model.default_entry_id is None

    def test_empty_entries_and_groups(self):
        """Empty lists are valid."""
        d = _make_payload_dict(groups=[], entries=[])
        model = StructureViewerPayloadModel.model_validate(d)
        assert model.groups == []
        assert model.entries == []

    def test_warnings_list(self):
        """Warnings list is preserved."""
        d = _make_payload_dict(warnings=["some warning", "another"])
        model = StructureViewerPayloadModel.model_validate(d)
        assert model.warnings == ["some warning", "another"]


# ── StructureViewerEntryModel ───────────────────────────────────────────────


class TestStructureViewerEntryModel:
    """Entry sub-model validation."""

    def test_minimal_entry(self):
        """Minimal entry with only required 'id' field."""
        model = StructureViewerEntryModel(id="test_id")
        assert model.id == "test_id"
        assert model.group_id == ""
        assert model.label == ""
        assert model.role == ""
        assert model.status == "completed"

    def test_entry_with_none_optional_sub_models(self):
        """Entry with None geometry/energy/source/vibrations uses defaults."""
        d = {"id": "e1"}
        model = StructureViewerEntryModel.model_validate(d)
        assert model.geometry.endpoint == ""
        assert model.energy.value is None
        assert model.source.kind == ""
        assert model.vibrations.available is False

    def test_entry_badges_list(self):
        """Badges round-trip as list."""
        d = _make_entry_dict(badges=["TS", "rank-1"])
        model = StructureViewerEntryModel.model_validate(d)
        assert model.badges == ["TS", "rank-1"]


# ── StructureViewerGroupModel ───────────────────────────────────────────────


class TestStructureViewerGroupModel:
    """Group sub-model validation."""

    def test_minimal_group(self):
        """Minimal group with only required 'id' field."""
        model = StructureViewerGroupModel(id="g1")
        assert model.id == "g1"
        assert model.label == ""
        assert model.kind == ""

    def test_full_group(self):
        d = _make_group_dict("pes_recommendations", "自动推荐", "recommendations")
        model = StructureViewerGroupModel.model_validate(d)
        assert model.id == "pes_recommendations"
        assert model.label == "自动推荐"
        assert model.kind == "recommendations"


# ── StructureViewerVibrationsResponse ───────────────────────────────────────


class TestStructureViewerVibrationsResponse:
    """Vibrations response model validation."""

    def test_defaults(self):
        """Default threshold_cm1=-50.0, threshold_source='default', modes=[]"""
        model = StructureViewerVibrationsResponse(available=False, atom_count=3)
        assert model.available is False
        assert model.reason is None
        assert model.threshold_cm1 == -50.0
        assert model.threshold_source == "default"
        assert model.modes == []
        assert model.atom_count == 3
        assert model.geometry_product_id is None

    def test_with_modes(self):
        mode = StructureViewerModeModel(
            mode_index=6,
            frequency_cm1=-797.72,
            imaginary=True,
            ir_intensity=24.8,
            vectors=[[0.01, -0.02, 0.03], [0.04, -0.05, 0.06]],
        )
        model = StructureViewerVibrationsResponse(
            available=True,
            atom_count=2,
            modes=[mode],
            geometry_product_id="batch_item_001",
        )
        assert model.available is True
        assert len(model.modes) == 1
        assert model.modes[0].mode_index == 6
        assert model.modes[0].frequency_cm1 == -797.72
        assert model.modes[0].imaginary is True
        assert model.modes[0].ir_intensity == 24.8
        assert len(model.modes[0].vectors) == 2
        assert model.geometry_product_id == "batch_item_001"

    def test_mode_ir_intensity_optional(self):
        """ir_intensity defaults to None."""
        mode = StructureViewerModeModel(
            mode_index=1,
            frequency_cm1=1000.0,
            imaginary=False,
            vectors=[[1.0, 2.0, 3.0]],
        )
        assert mode.ir_intensity is None


# ── StructureAssetCreateRequest backward compat ─────────────────────────────


class TestStructureAssetCreateRequestCompat:
    """StructureAssetCreateRequest backward compatibility."""

    def test_legacy_payload_valid(self):
        """Legacy payload without new fields still validates."""
        d = {"xyz_text": "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n"}
        model = StructureAssetCreateRequest.model_validate(d)
        assert model.xyz_text == d["xyz_text"]
        assert model.name == ""
        assert model.charge == 0
        assert model.multiplicity == 1
        assert model.project_id is None
        # New optional fields have defaults
        assert model.parent_job_id is None
        assert model.parent_entry_id is None
        assert model.edit_operations == []
        assert model.provenance == {}

    def test_new_fields_accepted(self):
        """New optional fields are accepted."""
        d = {
            "xyz_text": "3\nwater\nO 0 0 0\nH 0 0 1\nH 0 1 0\n",
            "parent_job_id": "j1",
            "parent_entry_id": "conf_0001",
            "edit_operations": [{"type": "bond_length", "atoms": [0, 1], "target": 1.5}],
            "provenance": {"source": "structure_viewer"},
        }
        model = StructureAssetCreateRequest.model_validate(d)
        assert model.parent_job_id == "j1"
        assert model.parent_entry_id == "conf_0001"
        assert len(model.edit_operations) == 1
        assert model.provenance == {"source": "structure_viewer"}

    def test_required_xyz_text_still_required(self):
        """Missing xyz_text still raises ValidationError."""
        with pytest.raises(ValidationError):
            StructureAssetCreateRequest.model_validate({"name": "test"})


# ── StructureAssetModel backward compat ─────────────────────────────────────


class TestStructureAssetModelCompat:
    """StructureAssetModel backward compatibility."""

    def test_legacy_payload_valid(self):
        """Legacy payload without metadata field still validates."""
        d = {
            "asset_id": "a1",
            "name": "test",
        }
        model = StructureAssetModel.model_validate(d)
        assert model.asset_id == "a1"
        assert model.metadata == {}

    def test_metadata_field_accepted(self):
        """New metadata field is accepted."""
        d = {
            "asset_id": "a1",
            "name": "test",
            "metadata": {"edit_source": "structure_viewer", "parent_job_id": "j1"},
        }
        model = StructureAssetModel.model_validate(d)
        assert model.metadata == {"edit_source": "structure_viewer", "parent_job_id": "j1"}


# ── Catalog endpoint tests (todo 8) ─────────────────────────────────────────

from collections.abc import Generator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus


def _make_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


@pytest.fixture()
def sv_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    with _make_client(tmp_path, monkeypatch) as c:
        yield c


def _seed_job(
    client: TestClient,
    tmp_path: Path,
    *,
    job_id: str = "sv-test-001",
    workflow: str = "Confsearch",
    status: JobStatus = JobStatus.COMPLETED,
    work_dir_name: str | None = None,
) -> Path:
    """Insert a job record into the store and return its work_dir path."""
    manager = client.app.state.job_manager
    work_dir = tmp_path / (work_dir_name or job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    record = JobRecord(
        id=job_id,
        spec=JobSpec(
            workflow=workflow,
            name=job_id,
            project_id=manager.default_project_id,
        ),
        status=status,
        work_dir=str(work_dir),
        project_id=manager.default_project_id,
    )
    manager.store.create(record)
    return work_dir


def _write_confsearch_manifest(work_dir: Path) -> None:
    """Write a minimal confsearch manifest with 3 conformers."""
    cs_dir = work_dir / "RESULT" / "confsearch"
    cs_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "conformers": [
            {
                "conf_id": "0001",
                "geometry": "conformers/0001.xyz",
                "energy_hartree": -100.0,
                "free_energy_hartree": -99.5,
                "relative_energy_kcal": 0.0,
                "boltzmann_weight": 0.6,
                "rank": 1,
            },
            {
                "conf_id": "0002",
                "geometry": "conformers/0002.xyz",
                "energy_hartree": -99.8,
                "free_energy_hartree": -99.3,
                "relative_energy_kcal": 1.25,
                "boltzmann_weight": 0.3,
                "rank": 2,
            },
            {
                "conf_id": "0003",
                "geometry": "conformers/0003.xyz",
                "energy_hartree": -99.6,
                "free_energy_hartree": -99.1,
                "relative_energy_kcal": 2.51,
                "boltzmann_weight": 0.1,
                "rank": 3,
            },
        ],
    }
    (cs_dir / "confsearch_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


def _write_batch_manifest(work_dir: Path, *, items: list[dict[str, Any]] | None = None) -> None:
    """Write a minimal result_manifest with batch structure products."""
    result_dir = work_dir / "RESULT"
    result_dir.mkdir(parents=True, exist_ok=True)
    products = items or [
        {
            "id": "batch_item_001",
            "label": "item_001 (TS, opt_freq_sp_thermo)",
            "path": "structures/item_001__TAG_TS__optimized.xyz",
            "kind": "structure",
        },
    ]
    manifest = {
        "schema_version": "result_manifest_v1",
        "workflow": "BatchOptimize",
        "products": products,
    }
    (result_dir / "result_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    struct_dir = result_dir / "structures"
    struct_dir.mkdir(parents=True, exist_ok=True)
    for p in products:
        xyz_path = result_dir / p["path"]
        xyz_path.parent.mkdir(parents=True, exist_ok=True)
        xyz_path.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")


class TestCatalogEndpoint:
    """GET /api/v1/jobs/{job_id}/structure-viewer"""

    def test_happy_confsearch_200(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Completed Confsearch job → 200 with schema_version, entries, availability=ready."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)

        resp = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == "structure_viewer_v1"
        assert body["job_id"] == "sv-test-001"
        assert body["workflow"] == "Confsearch"
        assert body["availability"] == "ready"
        assert len(body["entries"]) == 3
        assert body["default_entry_id"] is not None

    def test_unknown_job_404(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Unknown job id → 404."""
        resp = sv_client.get("/api/v1/jobs/nonexistent/structure-viewer")
        assert resp.status_code == 404
        body = resp.json()
        assert "not found" in body["detail"].lower()

    def test_unknown_item_id_404(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Unknown item_id on BatchOptimize → 404."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-batch-001",
            workflow="BatchOptimize",
        )
        _write_batch_manifest(work_dir)

        resp = sv_client.get("/api/v1/jobs/sv-batch-001/structure-viewer?item_id=nonexistent")
        assert resp.status_code == 404

    def test_item_id_filter_happy(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Valid item_id filter → single entry returned."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-batch-002",
            workflow="BatchOptimize",
        )
        _write_batch_manifest(work_dir, items=[
            {
                "id": "batch_item_001",
                "label": "item_001 (TS, opt_freq_sp_thermo)",
                "path": "structures/item_001__TAG_TS__optimized.xyz",
                "kind": "structure",
            },
            {
                "id": "batch_item_002",
                "label": "item_002 (INT, opt_freq_sp_thermo)",
                "path": "structures/item_002__TAG_INT__optimized.xyz",
                "kind": "structure",
            },
        ]        )
        for item_id in ("item_001", "item_002"):
            xyz_path = work_dir / "RESULT" / "structures" / f"{item_id}__TAG_TS__optimized.xyz"
            xyz_path.parent.mkdir(parents=True, exist_ok=True)
            xyz_path.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")

        resp = sv_client.get("/api/v1/jobs/sv-batch-002/structure-viewer?item_id=item_001")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["entries"]) == 1
        assert body["entries"][0]["id"] == "batch_item_001"
