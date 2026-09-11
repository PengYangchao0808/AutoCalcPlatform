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


# ── Geometry endpoint tests (todo 9) ────────────────────────────────────────


def _write_confsearch_manifest_with_xyz(work_dir: Path) -> None:
    """Write confsearch manifest + actual XYZ geometry files."""
    _write_confsearch_manifest(work_dir)
    conf_dir = work_dir / "RESULT" / "confsearch" / "conformers"
    conf_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, 4):
        xyz = f"3\n\nC 0 0 0\nH 0 0 {i}\nH 0 {i} 0\n"
        (conf_dir / f"000{i}.xyz").write_text(xyz, encoding="utf-8")


class TestGeometryEndpoint:
    """GET /api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/geometry"""

    def test_confsearch_rank1_geometry_200(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Confsearch rank-1 geometry → 200 text/plain, starts with atom count."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]
        assert default_id is not None

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/geometry"
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        body = resp.text
        first_line = body.strip().splitlines()[0]
        assert first_line.isdigit()
        assert int(first_line) == 3

    def test_unknown_entry_404(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Unknown entry id → 404."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        resp = sv_client.get(
            "/api/v1/jobs/sv-test-001/structure-viewer/entries/nonexistent/geometry"
        )
        assert resp.status_code == 404

    def test_path_escape_404(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Path-escape via crafted product path → 404, no file contents leak."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-escape-001",
            workflow="BatchOptimize",
        )
        _write_batch_manifest(work_dir, items=[
            {
                "id": "batch_evil",
                "label": "evil",
                "path": "structures/../../../../../../etc/passwd",
                "kind": "structure",
            },
        ])
        (work_dir / "RESULT" / "structures").mkdir(parents=True, exist_ok=True)

        resp = sv_client.get(
            "/api/v1/jobs/sv-escape-001/structure-viewer/entries/batch_evil/geometry"
        )
        assert resp.status_code == 404
        assert "root:" not in resp.text

    def test_missing_file_404(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Entry exists but geometry file missing → 404."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/geometry"
        )
        assert resp.status_code == 404

    def test_multi_frame_irc_first_frame(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Multi-frame IRC file → first frame returned."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-irc-001",
            workflow="irc",
        )
        irc_dir = work_dir / "RESULT" / "irc"
        irc_dir.mkdir(parents=True, exist_ok=True)
        frame1 = "2\nframe 0\nC 0 0 0\nH 0 0 1\n"
        frame2 = "2\nframe 1\nC 0 0 0\nH 0 0 2\n"
        (irc_dir / "irc_forward.xyz").write_text(frame1 + frame2, encoding="utf-8")

        catalog = sv_client.get("/api/v1/jobs/sv-irc-001/structure-viewer").json()
        entry_id = None
        for e in catalog["entries"]:
            if e["id"].startswith("irc_"):
                entry_id = e["id"]
                break
        assert entry_id is not None

        resp = sv_client.get(
            f"/api/v1/jobs/sv-irc-001/structure-viewer/entries/{entry_id}/geometry"
        )
        assert resp.status_code == 200
        body = resp.text.strip()
        lines = body.splitlines()
        assert lines[0] == "2"
        assert "frame 0" in lines[1]
        assert "frame 1" not in body

    def test_irc_per_frame_geometry_third_frame(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Todo 39: irc_forward_2 → exactly the THIRD frame block."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-irc-003",
            workflow="irc",
        )
        irc_dir = work_dir / "RESULT" / "irc"
        irc_dir.mkdir(parents=True, exist_ok=True)
        frames = "".join(
            f"2\nframe {i}\nC 0 0 {i}\nH 0 0 {i + 1}\n" for i in range(3)
        )
        (irc_dir / "irc_forward.xyz").write_text(frames, encoding="utf-8")

        catalog = sv_client.get("/api/v1/jobs/sv-irc-003/structure-viewer").json()
        ids = [e["id"] for e in catalog["entries"] if e["id"].startswith("irc_forward")]
        assert ids == ["irc_forward_0", "irc_forward_1", "irc_forward_2"]

        resp = sv_client.get(
            "/api/v1/jobs/sv-irc-003/structure-viewer/entries/irc_forward_2/geometry"
        )
        assert resp.status_code == 200
        body = resp.text.strip()
        assert "frame 2" in body
        assert "frame 0" not in body
        assert "frame 1" not in body


# ── Vibrations endpoint tests (todo 10) ─────────────────────────────────────


def _make_normal_modes_json(
    *,
    atom_count: int = 3,
    geometry_product_id: str | None = None,
    modes: list[dict] | None = None,
) -> dict:
    """Build a normal_modes_v1 fixture matching doc §4.2."""
    return {
        "schema_version": "normal_modes_v1",
        "units": {"frequency": "cm-1", "displacement": "dimensionless_orca_normal_mode"},
        "atom_count": atom_count,
        "geometry_product_id": geometry_product_id,
        "modes": modes or [
            {
                "mode_index": 6,
                "frequency_cm1": -797.72,
                "imaginary": True,
                "ir_intensity": 24.8,
                "vectors": [[0.01, -0.02, 0.03], [0.04, -0.05, 0.06], [0.07, -0.08, 0.09]],
            },
            {
                "mode_index": 7,
                "frequency_cm1": 500.0,
                "imaginary": False,
                "ir_intensity": 10.0,
                "vectors": [[0.1, 0.0, 0.0], [0.0, 0.1, 0.0], [0.0, 0.0, 0.1]],
            },
            {
                "mode_index": 8,
                "frequency_cm1": 1200.5,
                "imaginary": False,
                "ir_intensity": None,
                "vectors": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            },
        ],
    }


def _write_normal_modes(work_dir: Path, data: dict, filename: str = "normal_modes.json") -> None:
    freq_dir = work_dir / "RESULT" / "frequencies"
    freq_dir.mkdir(parents=True, exist_ok=True)
    (freq_dir / filename).write_text(json.dumps(data), encoding="utf-8")


class TestVibrationsEndpoint:
    """GET /api/v1/jobs/{job_id}/structure-viewer/entries/{entry_id}/vibrations"""

    def test_valid_normal_modes_available_true(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Fixture with 3 modes (incl. 1 imaginary) → available=true, modes round-trip."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        _write_normal_modes(work_dir, _make_normal_modes_json())

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["reason"] is None
        assert body["threshold_cm1"] == -50.0
        assert body["threshold_source"] == "default"
        assert body["atom_count"] == 3
        assert len(body["modes"]) == 3

        imaginary_mode = next(m for m in body["modes"] if m["imaginary"])
        assert imaginary_mode["mode_index"] == 6
        assert imaginary_mode["frequency_cm1"] == -797.72
        assert imaginary_mode["ir_intensity"] == 24.8
        assert len(imaginary_mode["vectors"]) == 3

        non_imag = next(m for m in body["modes"] if m["mode_index"] == 8)
        assert non_imag["ir_intensity"] is None

    def test_no_product_available_false(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """No normal_modes.json → 200, available=false, reason=no_normal_modes."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "no_normal_modes"
        assert body["modes"] == []
        assert body["atom_count"] == 0

    def test_malformed_json_available_false(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Malformed JSON → 200, available=false (never 500)."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        freq_dir = work_dir / "RESULT" / "frequencies"
        freq_dir.mkdir(parents=True, exist_ok=True)
        (freq_dir / "normal_modes.json").write_text("NOT JSON {{{", encoding="utf-8")

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "no_normal_modes"

    def test_wrong_schema_version_available_false(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Wrong schema_version → 200, available=false."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        bad = _make_normal_modes_json()
        bad["schema_version"] = "wrong_version"
        _write_normal_modes(work_dir, bad)

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False

    def test_unknown_entry_404(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Unknown entry id → 404."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        resp = sv_client.get(
            "/api/v1/jobs/sv-test-001/structure-viewer/entries/nonexistent/vibrations"
        )
        assert resp.status_code == 404

    def test_batch_entry_probes_item_scoped_file(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Batch entry probes {item_id}__normal_modes.json first."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-batch-vib-001",
            workflow="BatchOptimize",
        )
        _write_batch_manifest(work_dir, items=[
            {
                "id": "batch_item_001",
                "label": "item_001 (TS, opt_freq_sp_thermo)",
                "path": "structures/item_001__TAG_TS__optimized.xyz",
                "kind": "structure",
            },
        ])
        xyz_path = work_dir / "RESULT" / "structures" / "item_001__TAG_TS__optimized.xyz"
        xyz_path.parent.mkdir(parents=True, exist_ok=True)
        xyz_path.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")

        item_data = _make_normal_modes_json(
            atom_count=3,
            geometry_product_id="batch_item_001",
            modes=[
                {
                    "mode_index": 1,
                    "frequency_cm1": 100.0,
                    "imaginary": False,
                    "ir_intensity": 5.0,
                    "vectors": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                },
            ],
        )
        _write_normal_modes(work_dir, item_data, filename="item_001__normal_modes.json")

        resp = sv_client.get(
            "/api/v1/jobs/sv-batch-vib-001/structure-viewer/entries/batch_item_001/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["atom_count"] == 3
        assert body["geometry_product_id"] == "batch_item_001"
        assert len(body["modes"]) == 1
        assert body["modes"][0]["frequency_cm1"] == 100.0


class TestHistoricalModeProjection:
    """Read-only fallback: parse ORCA output on-the-fly when normal_modes.json is absent."""

    def test_historical_projection_returns_modes(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """ORCA output in WORK/04_FREQ/ → available=true, source=historical_projection."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        freq_dir = work_dir / "WORK" / "04_FREQ"
        freq_dir.mkdir(parents=True, exist_ok=True)
        from tests.test_acp_frequency_modes import FULL_MODES_FIXTURE

        (freq_dir / "orca_freq.out").write_text(
            FULL_MODES_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8"
        )

        snapshots_before = {
            p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in freq_dir.rglob("*")
            if p.is_file()
        }

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["source"] == "historical_projection"
        assert body["atom_count"] == 3
        assert len(body["modes"]) == 9

        imaginary = [m for m in body["modes"] if m["imaginary"]]
        assert len(imaginary) == 2

        snapshots_after = {
            p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in freq_dir.rglob("*")
            if p.is_file()
        }
        assert snapshots_before == snapshots_after

    def test_historical_projection_no_orca_output(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """No ORCA output → available=false, reason=no_normal_modes."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "no_normal_modes"

    def test_product_source_when_normal_modes_json_exists(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """normal_modes.json present → source=product (not historical_projection)."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        _write_normal_modes(work_dir, _make_normal_modes_json())

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["source"] == "product"


class TestThresholdSource:
    """Configurable significant-imaginary threshold (todo 26)."""

    def test_default_threshold_source(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """No imaginary_threshold_cm1 in job method → threshold=-50.0, source=default."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        _write_normal_modes(work_dir, _make_normal_modes_json())

        catalog = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-test-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["threshold_cm1"] == -50.0
        assert body["threshold_source"] == "default"

    def test_job_config_threshold_source(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """imaginary_threshold_cm1 in job method → threshold from config, source=job_config."""
        manager = sv_client.app.state.job_manager
        work_dir = tmp_path / "sv-threshold-001"
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id="sv-threshold-001",
            spec=JobSpec(
                workflow="Confsearch",
                name="sv-threshold-001",
                project_id=manager.default_project_id,
                method={"imaginary_threshold_cm1": -30.0},
            ),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
        )
        manager.store.create(record)
        _write_confsearch_manifest_with_xyz(work_dir)
        _write_normal_modes(work_dir, _make_normal_modes_json())

        catalog = sv_client.get("/api/v1/jobs/sv-threshold-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/sv-threshold-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["threshold_cm1"] == -30.0
        assert body["threshold_source"] == "job_config"


# ── Remote structure cache tests (todo 11) ─────────────────────────────────


class TestRemoteStructureCache:
    """Unit tests for RemoteStructureCache (no network, no API)."""

    def test_cache_path_basic(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        p = cache.cache_path("job-1", "RESULT/confsearch/confsearch_manifest.json")
        assert p == tmp_path / ".remote_cache" / "job-1" / "RESULT" / "confsearch" / "confsearch_manifest.json"

    def test_cache_path_rejects_escape(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        with pytest.raises(ValueError, match="escapes"):
            cache.cache_path("job-1", "../../etc/passwd")

    def test_cache_path_rejects_dot_dot_in_middle(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        with pytest.raises(ValueError, match="escapes"):
            cache.cache_path("job-1", "RESULT/../../../etc/passwd")

    def test_get_cached_miss(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        assert cache.get_cached("job-1", "RESULT/x.json") is None

    def test_get_cached_hit(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        target = cache.cache_path("job-1", "RESULT/x.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"ok":true}', encoding="utf-8")
        assert cache.get_cached("job-1", "RESULT/x.json") == target

    def test_fetch_writes_atomically(self, tmp_path: Path) -> None:
        """fetch() writes file atomically (no partial files on success)."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        content = b"3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n"

        class FakeFetcher:
            def read_file(self, record, filename: str) -> bytes:
                return content

        cache = RemoteStructureCache(tmp_path, fetcher_factory=lambda job_id: FakeFetcher())

        class FakeRecord:
            id = "job-1"
            result = {"node": "n1", "remote_dir": "/remote/job-1"}

        result = cache.fetch(FakeRecord(), "RESULT/structures/geom.xyz")  # type: ignore[arg-type]
        assert result is not None
        assert result.read_bytes() == content
        # No tmp files left behind
        assert not list(result.parent.glob("*.tmp"))

    def test_fetch_returns_cached(self, tmp_path: Path) -> None:
        """fetch() returns cached path without calling fetcher on second call."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        call_count = 0

        class FakeFetcher:
            def read_file(self, record, filename: str) -> bytes:
                nonlocal call_count
                call_count += 1
                return b"data"

        cache = RemoteStructureCache(tmp_path, fetcher_factory=lambda jid: FakeFetcher())

        class FakeRecord:
            id = "j1"
            result = {"node": "n1", "remote_dir": "/r"}

        r1 = cache.fetch(FakeRecord(), "RESULT/x.json")  # type: ignore[arg-type]
        r2 = cache.fetch(FakeRecord(), "RESULT/x.json")  # type: ignore[arg-type]
        assert r1 == r2
        assert call_count == 1

    def test_fetch_no_write_inside_task_dir(self, tmp_path: Path) -> None:
        """fetch() must NOT write inside the task work_dir."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        work_dir = tmp_path / "projects" / "default" / "job-1"
        work_dir.mkdir(parents=True)
        (work_dir / "RESULT").mkdir()
        initial_files = set(work_dir.rglob("*"))

        class FakeFetcher:
            def read_file(self, record, filename: str) -> bytes:
                return b"content"

        cache = RemoteStructureCache(tmp_path, fetcher_factory=lambda jid: FakeFetcher())

        record = type("FakeRecord", (), {
            "id": "job-1",
            "result": {"node": "n1", "remote_dir": "/remote"},
            "work_dir": str(work_dir),
        })()

        cache.fetch(record, "RESULT/test.json")
        final_files = set(work_dir.rglob("*"))
        assert initial_files == final_files

    def test_purge_job_removes_dir(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        target = cache.cache_path("job-1", "RESULT/x.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("data", encoding="utf-8")
        assert target.exists()
        cache.purge_job("job-1")
        assert not target.parent.parent.exists()

    def test_sweep_expired_removes_old(self, tmp_path: Path) -> None:
        import os
        import time

        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        target = cache.cache_path("old-job", "RESULT/x.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("data", encoding="utf-8")
        # Set mtime on the job directory to 8 days ago
        job_dir = cache._cache_root / "old-job"
        old_time = time.time() - 8 * 86400
        os.utime(str(job_dir), (old_time, old_time))
        removed = cache.sweep_expired(ttl_days=7)
        assert removed >= 1
        assert not job_dir.exists()

    def test_sweep_expired_keeps_fresh(self, tmp_path: Path) -> None:
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        target = cache.cache_path("new-job", "RESULT/x.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("data", encoding="utf-8")
        removed = cache.sweep_expired(ttl_days=7)
        assert removed == 0
        assert target.exists()

    def test_required_files_absent_confsearch(self, tmp_path: Path) -> None:
        """Confsearch: manifest missing → True."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        assert cache.required_files_absent(tmp_path, "Confsearch") is True

    def test_required_files_absent_confsearch_present(self, tmp_path: Path) -> None:
        """Confsearch: manifest present → False."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        cs_dir = tmp_path / "RESULT" / "confsearch"
        cs_dir.mkdir(parents=True)
        (cs_dir / "confsearch_manifest.json").write_text("{}", encoding="utf-8")
        cache = RemoteStructureCache(tmp_path)
        assert cache.required_files_absent(tmp_path, "Confsearch") is False

    def test_required_files_absent_batch(self, tmp_path: Path) -> None:
        """BatchOptimize: result_manifest.json missing → True."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        assert cache.required_files_absent(tmp_path, "BatchOptimize") is True

    def test_required_files_absent_scan(self, tmp_path: Path) -> None:
        """scan: scan_trajectory.json missing → True."""
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(tmp_path)
        assert cache.required_files_absent(tmp_path, "scan") is True


class TestRemoteAvailabilityEndpoints:
    """Integration tests for remote job availability in structure-viewer endpoints."""

    def _seed_remote_job(
        self,
        client: TestClient,
        tmp_path: Path,
        *,
        job_id: str = "remote-001",
        workflow: str = "Confsearch",
    ) -> Path:
        """Seed a remote job (has node_id/host/result.node)."""
        manager = client.app.state.job_manager
        work_dir = tmp_path / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        record = JobRecord(
            id=job_id,
            spec=JobSpec(workflow=workflow, name=job_id, project_id=manager.default_project_id),
            status=JobStatus.COMPLETED,
            work_dir=str(work_dir),
            project_id=manager.default_project_id,
            node_id="node1",
            host="compute-01",
            result={"node": "node1", "remote_dir": f"/remote/{job_id}"},
        )
        manager.store.create(record)
        return work_dir

    def test_catalog_pending_fetch_when_remote_unsynced(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Remote job with required files absent → availability=pending_fetch."""
        self._seed_remote_job(sv_client, tmp_path)
        resp = sv_client.get("/api/v1/jobs/remote-001/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability"] == "pending_fetch"
        assert any("pending_fetch" in w for w in body["warnings"])

    def test_catalog_ready_when_files_present(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Remote job with files on disk → availability=ready."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        resp = sv_client.get("/api/v1/jobs/remote-001/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["availability"] == "ready"

    def test_geometry_409_pending_fetch_when_unsynced(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Remote job geometry absent + no fetch param → 409 pending_fetch."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)
        catalog = sv_client.get("/api/v1/jobs/remote-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]
        resp = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/geometry"
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "pending_fetch"

    def test_geometry_fetch_param_triggers_download(
        self, sv_client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Geometry ?fetch=1 with FakeFetcher → 200, file lands in cache, NOT in task dir."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)
        xyz_content = "3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n"

        # Patch the cache fetcher factory on the manager
        manager = sv_client.app.state.job_manager

        class FakeFetcher:
            def read_file(self, record, filename: str) -> bytes:
                return xyz_content.encode("utf-8")

        # Inject a cache with our fake fetcher
        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(
            manager.run_root,
            fetcher_factory=lambda jid: FakeFetcher(),
        )
        manager._remote_structure_cache = cache  # type: ignore[attr-defined]

        catalog = sv_client.get("/api/v1/jobs/remote-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        # Snapshot task dir before
        work_dir_path = Path(work_dir)
        task_files_before = set(work_dir_path.rglob("*"))

        resp = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/geometry?fetch=1"
        )
        assert resp.status_code == 200
        assert resp.text.strip().startswith("3")

        # Verify NO writes inside task dir
        task_files_after = set(work_dir_path.rglob("*"))
        assert task_files_before == task_files_after

        # Verify cache was populated
        assert cache.get_cached("remote-001", f"RESULT/confsearch/conformers/0001.xyz") is not None

    def test_geometry_retry_after_fetch_succeeds(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """After fetch=1 populates cache, retry without fetch → 200 from cache."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)
        xyz_content = "3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n"

        manager = sv_client.app.state.job_manager

        class FakeFetcher:
            def read_file(self, record, filename: str) -> bytes:
                return xyz_content.encode("utf-8")

        from acp.results.remote_structure_cache import RemoteStructureCache

        cache = RemoteStructureCache(
            manager.run_root,
            fetcher_factory=lambda jid: FakeFetcher(),
        )
        manager._remote_structure_cache = cache  # type: ignore[attr-defined]

        catalog = sv_client.get("/api/v1/jobs/remote-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        # First: fetch
        resp1 = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/geometry?fetch=1"
        )
        assert resp1.status_code == 200

        # Second: retry without fetch → still 200 from cache
        resp2 = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/geometry"
        )
        assert resp2.status_code == 200
        assert resp2.text.strip().startswith("3")

    def test_vibrations_pending_fetch_when_unsynced(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Remote job vibrations absent → available=false, reason=pending_fetch."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest_with_xyz(work_dir)
        catalog = sv_client.get("/api/v1/jobs/remote-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]
        resp = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/vibrations"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert body["reason"] == "pending_fetch"

    def test_geometry_fetch_uses_production_fetcher(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """Production path: inject _remote_fetcher on manager (no manual cache) → fetch=1 works."""
        work_dir = self._seed_remote_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)
        xyz_content = "3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n"

        manager = sv_client.app.state.job_manager

        class FakeFetcher:
            def read_file(self, record: Any, filename: str) -> bytes:
                return xyz_content.encode("utf-8")

        manager._remote_fetcher = FakeFetcher()  # type: ignore[attr-defined]

        catalog = sv_client.get("/api/v1/jobs/remote-001/structure-viewer").json()
        default_id = catalog["default_entry_id"]

        resp = sv_client.get(
            f"/api/v1/jobs/remote-001/structure-viewer/entries/{default_id}/geometry?fetch=1"
        )
        assert resp.status_code == 200
        assert resp.text.strip().startswith("3")


# ── Additional coverage tests (todo 12 gap-fill) ───────────────────────────


class TestCatalogAdditionalCoverage:
    """Additional catalog endpoint coverage for todo 12 gap-fill."""

    def test_catalog_revision_stable(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Two identical catalog calls → same revision (stability)."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)

        resp1 = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer")
        resp2 = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer")
        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json()["revision"] == resp2.json()["revision"]

    def test_catalog_default_entry_id_present(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Catalog response includes a non-None default_entry_id."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)

        resp = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_entry_id"] is not None
        assert isinstance(body["default_entry_id"], str)
        assert len(body["default_entry_id"]) > 0

    def test_catalog_warnings_list(self, sv_client: TestClient, tmp_path: Path) -> None:
        """Catalog response includes a warnings list (even if empty)."""
        work_dir = _seed_job(sv_client, tmp_path)
        _write_confsearch_manifest(work_dir)

        resp = sv_client.get("/api/v1/jobs/sv-test-001/structure-viewer")
        assert resp.status_code == 200
        body = resp.json()
        assert "warnings" in body
        assert isinstance(body["warnings"], list)

    def test_item_id_filter_excludes_other_items(
        self, sv_client: TestClient, tmp_path: Path
    ) -> None:
        """item_id filter returns only the requested item, not others."""
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-batch-exclude-001",
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
        ])
        for item_id in ("item_001", "item_002"):
            xyz_path = work_dir / "RESULT" / "structures" / f"{item_id}__TAG_TS__optimized.xyz"
            xyz_path.parent.mkdir(parents=True, exist_ok=True)
            xyz_path.write_text("3\n\nC 0 0 0\nH 0 0 1\nH 0 1 0\n", encoding="utf-8")

        resp = sv_client.get("/api/v1/jobs/sv-batch-exclude-001/structure-viewer?item_id=item_001")
        assert resp.status_code == 200
        body = resp.json()
        entry_ids = [e["id"] for e in body["entries"]]
        assert "batch_item_001" in entry_ids
        assert "batch_item_002" not in entry_ids

    def test_catalog_no_410_for_retired(self, sv_client: TestClient, tmp_path: Path) -> None:
        """No structure-viewer endpoint returns 410 — retired jobs served read-only at 200.

        By design, the structure viewer DISPLAYS retired/legacy jobs rather
        than blocking them with 410 (which is reserved for mutation endpoints
        like mechanism review). This test documents that decision.
        """
        work_dir = _seed_job(
            sv_client, tmp_path,
            job_id="sv-retired-001",
            workflow="ensemble",
        )
        _write_confsearch_manifest(work_dir)

        resp = sv_client.get("/api/v1/jobs/sv-retired-001/structure-viewer")
        assert resp.status_code == 200
        assert resp.json()["schema_version"] == "structure_viewer_v1"


# ── Todo 37: structure-asset edit-provenance metadata persistence ──


def test_asset_edit_metadata(sv_client: TestClient, tmp_path: Path) -> None:
    """POST /structure-assets persists parent ids + the full edit_operations
    list + provenance into metadata (response round-trip + sidecar on disk);
    legacy posts without the fields stay valid with empty metadata."""
    xyz = "3\nedited\nC 0.0 0.0 0.0\nH 1.1 0.0 0.0\nH -0.4 0.9 0.0\n"
    ops = [
        {
            "type": "bond_length",
            "atom_ids": [0, 1],
            "before": [[0, 0, 0], [1.2, 0, 0]],
            "after": [[0, 0, 0], [1.1, 0, 0]],
            "moved_atom_ids": [1],
            "collision_warnings": [],
        },
        {
            "type": "dihedral",
            "atom_ids": [0, 1, 2, 3],
            "before": [],
            "after": [],
            "moved_atom_ids": [2, 3],
            "collision_warnings": [],
        },
    ]
    prov = {"comment": "ACP edit: parent=job1 entry=e1 ops=2", "created_at": "2026-09-11T00:00:00", "op_count": 2}
    resp = sv_client.post(
        "/api/v1/structure-assets",
        json={
            "name": "edited-asset",
            "xyz_text": xyz,
            "parent_job_id": "job1",
            "parent_entry_id": "e1",
            "edit_operations": ops,
            "provenance": prov,
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    meta = body["metadata"]
    assert meta["parent_job_id"] == "job1"
    assert meta["parent_entry_id"] == "e1"
    assert meta["edit_operations"] == ops
    assert meta["provenance"] == prov

    # metadata sidecar persisted next to the stored upload (run_root-relative)
    asset_path = tmp_path / body["asset_path"]
    meta_file = asset_path.parent.parent / "metadata.json"
    assert meta_file.exists(), f"metadata.json missing at {meta_file}"
    stored = json.loads(meta_file.read_text(encoding="utf-8"))
    assert stored["parent_job_id"] == "job1"
    assert len(stored["edit_operations"]) == 2
    assert stored["edit_operations"][1]["type"] == "dihedral"


def test_asset_legacy_post_without_metadata(sv_client: TestClient) -> None:
    """Legacy POST (no extended fields) still succeeds with empty metadata."""
    xyz = "2\nplain\nC 0.0 0.0 0.0\nH 1.1 0.0 0.0\n"
    resp = sv_client.post(
        "/api/v1/structure-assets",
        json={"name": "plain", "xyz_text": xyz},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["metadata"] == {}
