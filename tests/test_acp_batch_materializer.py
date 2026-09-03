"""Tests for the batch_structures input materializer and BatchOptimize --from-job."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.calculations.batch.loaders import load_batch_request
from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
from acp.scheduler.runner import JobRunner, materialize_job_input

_XYZ = "3\nmol\nO 0.0 0.0 0.0\nH 0.0 0.0 0.96\nH 0.0 0.0 -0.96\n"


def _batch_payload() -> dict:
    return {
        "source_type": "batch_structures",
        "charge": 0,
        "multiplicity": 1,
        "items": [
            {
                "name": "frame_027__TAG_TS",
                "tag": "TS",
                "candidate_id": "pes_ts_frame_027",
                "charge": 0,
                "multiplicity": 1,
                "include": True,
                "xyz": _XYZ,
            },
            {
                "name": "frame_036__TAG_INT",
                "tag": "",
                "candidate_id": "pes_int_frame_036",
                "charge": -1,
                "multiplicity": 2,
                "xyz": _XYZ,
            },
        ],
    }


class TestMaterializeBatchStructures:
    def test_materializes_items_json(self, tmp_path: Path) -> None:
        inputs_dir = tmp_path / "inputs"
        dest = materialize_job_input(_batch_payload(), inputs_dir, tmp_path)

        assert dest is not None
        assert dest.name == "batch_items.json"
        assert dest.parent == inputs_dir

    def test_roundtrip_through_batch_loader(self, tmp_path: Path) -> None:
        dest = materialize_job_input(_batch_payload(), tmp_path / "inputs", tmp_path)
        assert dest is not None

        items = load_batch_request(dest)
        assert [item.candidate_id for item in items] == [
            "pes_ts_frame_027",
            "pes_int_frame_036",
        ]
        assert [item.tag for item in items] == ["TS", "INT"]
        assert [item.name for item in items] == ["frame_027__TAG_TS", "frame_036__TAG_INT"]
        assert items[1].charge == -1
        assert items[1].multiplicity == 2
        assert all(item.include for item in items)

    def test_build_cmd_uses_materialized_items_file(self, tmp_path: Path) -> None:
        dest = materialize_job_input(_batch_payload(), tmp_path / "inputs", tmp_path)
        assert dest is not None
        spec = JobSpec(
            workflow="BatchOptimize",
            name="pes_batch",
            input=_batch_payload(),
            method={"profile": "opt_freq_sp_thermo"},
        )
        cmd = JobRunner(python_executable="python")._build_cmd(spec, tmp_path, str(dest))

        assert "--items-file" in cmd
        assert str(dest) in cmd
        assert "--layout-mode" in cmd and "single_flat" in cmd
        assert "--profile" in cmd and "opt_freq_sp_thermo" in cmd

    def test_empty_items_fall_through(self, tmp_path: Path) -> None:
        payload = {"source_type": "batch_structures", "items": [{"name": "no_xyz"}]}
        assert materialize_job_input(payload, tmp_path, tmp_path) is None

    def test_include_false_is_preserved(self, tmp_path: Path) -> None:
        payload = _batch_payload()
        payload["items"][1]["include"] = False
        dest = materialize_job_input(payload, tmp_path / "inputs", tmp_path)
        assert dest is not None

        items = load_batch_request(dest)
        assert [item.candidate_id for item in items] == ["pes_ts_frame_027"]


class TestBatchFromJob:
    def _store_job(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.setenv("ACP_RUN_ROOT", str(tmp_path))
        from acp.core.paths import resolve_run_root
        from acp.scheduler.store import JobStore

        work_dir = tmp_path / "pes_task"
        pes_dir = work_dir / "RESULT"
        pes_dir.mkdir(parents=True)
        (pes_dir / "result_manifest.json").write_text("{}", encoding="utf-8")
        store = JobStore(resolve_run_root() / "acp_jobs.db")
        store.create(
            JobRecord(
                id="20260903_001_PESsearch",
                spec=JobSpec(workflow="PESsearch", name="pes"),
                status=JobStatus.COMPLETED,
                work_dir=str(work_dir),
            )
        )
        return work_dir

    def test_resolves_default_manifest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from acp.cli import _resolve_batch_artifact_from_job

        work_dir = self._store_job(tmp_path, monkeypatch)
        resolved = _resolve_batch_artifact_from_job("20260903_001_PESsearch", None)
        assert resolved == (work_dir / "RESULT" / "result_manifest.json").resolve()

    def test_missing_job_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from acp.cli import _resolve_batch_artifact_from_job

        self._store_job(tmp_path, monkeypatch)
        with pytest.raises(FileNotFoundError, match="Job not found"):
            _resolve_batch_artifact_from_job("missing_job", None)

    def test_parser_accepts_from_job(self) -> None:
        from acp.cli import build_parser

        args = build_parser().parse_args(
            ["run", "BatchOptimize", "--from-job", "20260903_001_PESsearch"]
        )
        assert args.from_job == "20260903_001_PESsearch"
        assert args.items_file is None

    def test_parser_rejects_conflicting_sources(self) -> None:
        from acp.cli import build_parser

        with pytest.raises(SystemExit):
            build_parser().parse_args(
                [
                    "run",
                    "BatchOptimize",
                    "--from-job",
                    "j1",
                    "--items-file",
                    "structures.xyz",
                ]
            )


class TestResolveBatchStructuresInput:
    def test_inlines_source_id_references(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from acp.api import v1_routes

        class FakeService:
            def get(self, source_id):
                assert source_id == "job_p1:RESULT/structures/ts.xyz"
                return {"xyz": _XYZ, "name": "ts"}, "sha256:abc"

        monkeypatch.setattr(v1_routes, "_structure_source_service", lambda request: FakeService())
        inp = {
            "source_type": "batch_structures",
            "items": [
                {"name": "a", "xyz": _XYZ},
                {"name": "b", "tag": "TS", "source_id": "job_p1:RESULT/structures/ts.xyz"},
            ],
        }
        resolved = v1_routes._resolve_batch_structures_input(inp, request=None)
        assert resolved["items"][0] == {"name": "a", "xyz": _XYZ}
        assert resolved["items"][1]["xyz"] == _XYZ
        assert "source_id" not in resolved["items"][1]
        assert resolved["items"][1]["tag"] == "TS"

    def test_passes_through_without_source_ids(self) -> None:
        from acp.api import v1_routes

        inp = {"source_type": "batch_structures", "items": [{"name": "a", "xyz": _XYZ}]}
        assert v1_routes._resolve_batch_structures_input(inp, request=None) is inp

    def test_bad_source_id_raises_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import HTTPException

        from acp.api import v1_routes

        class FakeService:
            def get(self, source_id):
                raise ValueError("Job not found: p1")

        monkeypatch.setattr(v1_routes, "_structure_source_service", lambda request: FakeService())
        inp = {"source_type": "batch_structures", "items": [{"source_id": "job_p1:x"}]}
        with pytest.raises(HTTPException) as exc_info:
            v1_routes._resolve_batch_structures_input(inp, request=None)
        assert exc_info.value.status_code == 422
