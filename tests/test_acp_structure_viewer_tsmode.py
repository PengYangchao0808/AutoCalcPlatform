"""Tests for acp.results.structure_viewer — tsmode resolver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.results.structure_viewer import build_structure_viewer_payload


def test_module_imports():
    from acp.results.structure_viewer import (  # noqa: F401
        StructureViewerEntry,
        build_structure_viewer_payload,
        tsmode_entry_id,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_XYZ = (
    "3\n"
    "TS optimized structure comment\n"
    "C 0.0 0.0 0.0\nH 1.0 0.0 0.0\nH -1.0 0.0 0.0\n"
)

_SAMPLE_NORMAL_MODES = {
    "schema_version": "normal_modes_v1",
    "modes": [
        {"index": 1, "frequency_cm": 1500.0, "ir_intensity": 10.0, "displacements": [[0.1, 0.0, 0.0]]},
    ],
}


def _make_tsmode_task(
    tmp_path: Path,
    *,
    optimized_xyz: str | None = None,
    normal_modes: dict | None = None,
    source_xyz: str | None = None,
    result_manifest: dict | None = None,
) -> Path:
    (tmp_path / "job.json").write_text("{}")
    (tmp_path / "task.json").write_text("{}")

    if result_manifest is not None:
        result_dir = tmp_path / "RESULT"
        result_dir.mkdir(exist_ok=True)
        (result_dir / "result_manifest.json").write_text(
            json.dumps(result_manifest), encoding="utf-8"
        )

    if optimized_xyz is not None or normal_modes is not None:
        tsmode_dir = tmp_path / "RESULT" / "tsmode"
        tsmode_dir.mkdir(parents=True, exist_ok=True)
        if optimized_xyz is not None:
            (tsmode_dir / "optimized.xyz").write_text(optimized_xyz, encoding="utf-8")
        if normal_modes is not None:
            (tsmode_dir / "normal_modes.json").write_text(
                json.dumps(normal_modes), encoding="utf-8"
            )

    if source_xyz is not None:
        src_dir = tmp_path / "INPUT" / "tsmode"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "source.xyz").write_text(source_xyz, encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Entry-id helper tests
# ---------------------------------------------------------------------------


class TestTsmodeEntryId:
    def test_basic(self):
        from acp.results.structure_viewer import tsmode_entry_id
        assert tsmode_entry_id("optimized") == "tsmode_optimized"

    def test_source(self):
        from acp.results.structure_viewer import tsmode_entry_id
        assert tsmode_entry_id("source") == "tsmode_source"

    def test_exported(self):
        import acp.results.structure_viewer as sv
        assert "tsmode_entry_id" in sv.__all__


# ---------------------------------------------------------------------------
# Resolver tests
# ---------------------------------------------------------------------------


class TestTsmodeResolver:
    def test_optimized_xyz_only(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 1
        entry = payload.entries[0]
        assert entry.id == "tsmode_optimized"
        assert entry.role == "transition_state"
        assert entry.status == "completed"
        assert entry.source.kind == "formal_result"
        assert entry.source.geometry_ref == "RESULT/tsmode/optimized.xyz"
        assert payload.default_entry_id == "tsmode_optimized"

    def test_xyz_label_from_comment(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].label == "TS optimized structure comment"

    def test_xyz_empty_comment_fallback(self, tmp_path: Path):
        xyz = "3\n\nC 0 0 0\nH 1 0 0\nH -1 0 0\n"
        task = _make_tsmode_task(tmp_path, optimized_xyz=xyz)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].label == "TS Mode 优化结果"

    def test_vibrations_available_with_normal_modes(self, tmp_path: Path):
        task = _make_tsmode_task(
            tmp_path,
            optimized_xyz=_SAMPLE_XYZ,
            normal_modes=_SAMPLE_NORMAL_MODES,
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        vib = payload.entries[0].vibrations
        assert vib.available is True
        assert "j1" in vib.endpoint
        assert "tsmode_optimized" in vib.endpoint
        assert vib.source == "product"

    def test_vibrations_unavailable_without_normal_modes(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert payload.entries[0].vibrations.available is False

    def test_source_entry_when_source_xyz_present(self, tmp_path: Path):
        task = _make_tsmode_task(
            tmp_path,
            optimized_xyz=_SAMPLE_XYZ,
            source_xyz="2\nsource\nC 0 0 0\nH 1 0 0\n",
        )
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 2
        source_entry = next(e for e in payload.entries if e.id == "tsmode_source")
        assert source_entry.label == "Source structure"
        assert source_entry.role == "minimum"
        assert source_entry.source.kind == "calculation_input"
        assert source_entry.source.geometry_ref == "INPUT/tsmode/source.xyz"

    def test_empty_dir_warning(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 0
        assert any("tsmode results not found" in w for w in payload.warnings)

    def test_no_warning_when_optimized_exists(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert not any("tsmode results not found" in w for w in payload.warnings)

    def test_geometry_endpoint_uses_job_id(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="my_job", workflow="tsmode", job_status="completed"
        )
        assert "my_job" in payload.entries[0].geometry.endpoint
        assert "tsmode_optimized" in payload.entries[0].geometry.endpoint

    def test_manifest_absent_still_works(self, tmp_path: Path):
        task = _make_tsmode_task(tmp_path, optimized_xyz=_SAMPLE_XYZ)
        payload = build_structure_viewer_payload(
            task, job_id="j1", workflow="tsmode", job_status="completed"
        )
        assert len(payload.entries) == 1
        assert payload.entries[0].id == "tsmode_optimized"

    def test_dispatch_table_registered(self):
        from acp.results.structure_viewer import _DISPATCH_TABLE
        assert "tsmode" in _DISPATCH_TABLE
