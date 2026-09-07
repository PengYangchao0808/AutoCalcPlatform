"""Tests for acp.results.frame_candidates — frame candidate materialization service."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from acp.results.frame_candidates import (
    FRAME_CANDIDATES_RELATIVE_PATH,
    FRAME_CANDIDATES_SCHEMA,
    FrameCandidateError,
    RevisionConflictError,
    list_frame_candidates,
    remove_frame_candidate,
    resolve_frame_geometry,
    save_frame_candidate,
)
from acp.storage.manifest import ResultManifest

# ---------------------------------------------------------------------------
# Fixtures — synthetic task roots per view_type
# ---------------------------------------------------------------------------

_SAMPLE_XYZ = (
    "3\nsample molecule\nC  0.000  0.000  0.000\nH  0.000  0.000  1.089\nH  0.000  0.943 -0.363\n"
)


def _write_scan_task(root: Path) -> None:
    """Create a minimal scan task tree with RESULT/trajectories/scan_trajectory.json."""
    result_traj = root / "RESULT" / "trajectories"
    result_traj.mkdir(parents=True, exist_ok=True)
    # Write two frame xyz files
    for idx in (0, 1):
        frame_path = result_traj / f"scan_frame_{idx:03d}.xyz"
        frame_path.write_text(_SAMPLE_XYZ, encoding="utf-8")
    trajectory = {
        "workflow": "scan",
        "frame_count": 2,
        "frames": [
            {"index": 0, "path": "trajectories/scan_frame_000.xyz", "energy_hartree": -100.0},
            {"index": 1, "path": "trajectories/scan_frame_001.xyz", "energy_hartree": -99.5},
        ],
    }
    (result_traj / "scan_trajectory.json").write_text(
        json.dumps(trajectory, indent=2), encoding="utf-8"
    )


def _write_optimization_task(root: Path) -> None:
    """Create a minimal optimization task tree with trajectory + geometry files."""
    opt_dir = root / "WORK" / "03_OPT"
    opt_dir.mkdir(parents=True, exist_ok=True)
    # Write geometry files for 3 cycles
    for idx in range(3):
        geom_path = opt_dir / f"cycle_{idx:03d}.xyz"
        geom_path.write_text(_SAMPLE_XYZ, encoding="utf-8")
    trajectory = {
        "status": "converged",
        "source": "ORCA output",
        "cycles": [
            {
                "scf_energy_hartree": -100.0 + idx * 0.01,
                "rms_gradient": 1e-3,
                "max_gradient": 5e-3,
                "rms_displacement": 1e-3,
                "max_displacement": 5e-3,
                "scf_iterations": 10,
                "geometry_ref": f"cycle_{idx:03d}.xyz",
            }
            for idx in range(3)
        ],
    }
    (opt_dir / "optimization_trajectory.json").write_text(
        json.dumps(trajectory, indent=2), encoding="utf-8"
    )


def _write_sampling_task(root: Path) -> None:
    """Create a minimal sampling task tree with WORK/02_SEARCH/xTB/traj.xyz."""
    traj_dir = root / "WORK" / "02_SEARCH" / "xTB"
    traj_dir.mkdir(parents=True, exist_ok=True)
    # Write a multi-frame traj.xyz (3 frames)
    frames_text = ""
    for i in range(3):
        frames_text += (
            f"3\n"
            f"md: {i * 0.1:.1f} ps  {(-100.0 - i * 0.5):.4f} (kcal/mol)\n"
            f"C  {i * 0.1:.3f}  0.000  0.000\n"
            f"H  0.000  0.000  1.089\n"
            f"H  0.000  0.943 -0.363\n"
        )
    (traj_dir / "traj.xyz").write_text(frames_text, encoding="utf-8")


def _write_conformer_task(root: Path) -> None:
    """Create a minimal confsearch task tree with confsearch_manifest.json."""
    conf_dir = root / "RESULT" / "confsearch"
    conformers_dir = conf_dir / "conformers"
    conformers_dir.mkdir(parents=True, exist_ok=True)
    for rank in (1, 2):
        xyz_path = conformers_dir / f"conf_{rank:04d}.xyz"
        xyz_path.write_text(_SAMPLE_XYZ, encoding="utf-8")
    manifest = {
        "schema_version": "confsearch_v1",
        "workflow": "Confsearch",
        "protocol": "censo-crest",
        "profile": "default",
        "refinement_policy": "screen",
        "backend": "native",
        "input": {},
        "sampling": {},
        "conformers": [
            {
                "conf_id": f"conf_{rank:04d}",
                "geometry": f"conformers/conf_{rank:04d}.xyz",
                "energy_hartree": -100.0 + rank * 0.5,
                "free_energy_hartree": -100.0 + rank * 0.5,
                "relative_energy_kcal": 0.0 if rank == 1 else 313.75,
                "boltzmann_weight": 0.99 if rank == 1 else 0.01,
                "rank": rank,
            }
            for rank in (1, 2)
        ],
        "selected_conformers": ["conf_0001"],
        "refinement": {},
        "provenance": {},
        "quality_gates": {},
    }
    (conf_dir / "confsearch_manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


@pytest.fixture()
def scan_task(tmp_path: Path) -> Path:
    _write_scan_task(tmp_path)
    return tmp_path


@pytest.fixture()
def opt_task(tmp_path: Path) -> Path:
    _write_optimization_task(tmp_path)
    return tmp_path


@pytest.fixture()
def sampling_task(tmp_path: Path) -> Path:
    _write_sampling_task(tmp_path)
    return tmp_path


@pytest.fixture()
def conformer_task(tmp_path: Path) -> Path:
    _write_conformer_task(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# resolve_frame_geometry tests
# ---------------------------------------------------------------------------


class TestResolveFrameGeometry:
    """Per-view_type geometry resolution."""

    def test_scan_frame(self, scan_task: Path) -> None:
        xyz = resolve_frame_geometry(scan_task, view_type="scan", frame_index=0, workflow="scan")
        assert "C" in xyz
        assert "sample molecule" in xyz

    def test_scan_frame_index_1(self, scan_task: Path) -> None:
        xyz = resolve_frame_geometry(scan_task, view_type="scan", frame_index=1, workflow="scan")
        assert "C" in xyz

    def test_scan_frame_missing_index(self, scan_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="frame"):
            resolve_frame_geometry(scan_task, view_type="scan", frame_index=999, workflow="scan")

    def test_optimization_frame(self, opt_task: Path) -> None:
        xyz = resolve_frame_geometry(
            opt_task, view_type="optimization", frame_index=2, workflow="optimize"
        )
        assert "C" in xyz
        assert "sample molecule" in xyz

    def test_optimization_frame_missing_trajectory(self, tmp_path: Path) -> None:
        with pytest.raises(FrameCandidateError, match="optimization trajectory"):
            resolve_frame_geometry(
                tmp_path, view_type="optimization", frame_index=0, workflow="optimize"
            )

    def test_sampling_frame(self, sampling_task: Path) -> None:
        xyz = resolve_frame_geometry(
            sampling_task, view_type="sampling", frame_index=0, workflow="xtb-md"
        )
        assert "C" in xyz

    def test_sampling_frame_out_of_range(self, sampling_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="frame"):
            resolve_frame_geometry(
                sampling_task, view_type="sampling", frame_index=999, workflow="xtb-md"
            )

    def test_conformer_frame(self, conformer_task: Path) -> None:
        # frame_index=0 → rank 1
        xyz = resolve_frame_geometry(
            conformer_task, view_type="conformer", frame_index=0, workflow="Confsearch"
        )
        assert "C" in xyz

    def test_conformer_frame_rank2(self, conformer_task: Path) -> None:
        # frame_index=1 → rank 2
        xyz = resolve_frame_geometry(
            conformer_task, view_type="conformer", frame_index=1, workflow="Confsearch"
        )
        assert "C" in xyz

    def test_conformer_frame_out_of_range(self, conformer_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="frame"):
            resolve_frame_geometry(
                conformer_task, view_type="conformer", frame_index=99, workflow="Confsearch"
            )

    def test_unknown_view_type(self, scan_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="view_type"):
            resolve_frame_geometry(scan_task, view_type="unknown", frame_index=0, workflow="scan")


# ---------------------------------------------------------------------------
# save_frame_candidate tests
# ---------------------------------------------------------------------------


class TestSaveFrameCandidate:
    """Save round-trips per view_type, idempotency, revision conflict."""

    def test_save_scan_candidate(self, scan_task: Path) -> None:
        result = save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        candidate_id = "scan_ts_frame_000"
        assert result["candidate_id"] == candidate_id
        assert result["revision"] == 1
        # Verify xyz was written
        xyz_path = scan_task / "RESULT" / "structures" / f"{candidate_id}.xyz"
        assert xyz_path.is_file()
        xyz_text = xyz_path.read_text(encoding="utf-8")
        assert f"candidate_id={candidate_id}" in xyz_text
        assert "TAG: TS" in xyz_text
        assert "source=scan" in xyz_text
        assert "selection_source=manual_frame" in xyz_text

    def test_save_optimization_candidate(self, opt_task: Path) -> None:
        result = save_frame_candidate(
            opt_task,
            job_id="test_job_002",
            workflow="optimize",
            view_type="optimization",
            frame_index=2,
            role="INT",
        )
        assert result["candidate_id"] == "opt_int_frame_002"
        assert result["revision"] == 1

    def test_save_sampling_candidate(self, sampling_task: Path) -> None:
        result = save_frame_candidate(
            sampling_task,
            job_id="test_job_003",
            workflow="xtb-md",
            view_type="sampling",
            frame_index=1,
            role="TS",
        )
        assert result["candidate_id"] == "md_ts_frame_001"

    def test_save_conformer_candidate(self, conformer_task: Path) -> None:
        result = save_frame_candidate(
            conformer_task,
            job_id="test_job_004",
            workflow="Confsearch",
            view_type="conformer",
            frame_index=0,
            role="NONE",
        )
        assert result["candidate_id"] == "conf_none_frame_000"

    def test_idempotent_re_save(self, scan_task: Path) -> None:
        first = save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        second = save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        # Same candidate_id, no duplicate
        assert first["candidate_id"] == second["candidate_id"]
        candidates = list_frame_candidates(scan_task)
        assert len(candidates["candidates"]) == 1
        # Revision incremented
        assert second["revision"] == 2

    def test_save_two_different_candidates(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=1,
            role="INT",
        )
        candidates = list_frame_candidates(scan_task)
        assert len(candidates["candidates"]) == 2

    def test_save_registers_in_manifest(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        manifest_path = scan_task / "RESULT" / "result_manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        products = manifest.get("products", [])
        frame_products = [p for p in products if p.get("id", "").startswith("frame_candidate_")]
        assert len(frame_products) == 1
        assert frame_products[0]["kind"] == "structure"
        assert frame_products[0]["metadata"]["candidate_id"] == "scan_ts_frame_000"
        assert frame_products[0]["metadata"]["selection_source"] == "manual_frame"

    def test_save_with_custom_name(self, scan_task: Path) -> None:
        result = save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="INT",
            name="My Custom Candidate",
        )
        assert result["candidate_id"] == "scan_int_frame_000"
        candidates = list_frame_candidates(scan_task)
        assert candidates["candidates"][0]["name"] == "My Custom Candidate"


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidation:
    """Input validation — PESsearch rejection, role validation, frame_index bounds."""

    def test_pessearch_workflow_rejected(self, scan_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="PESsearch"):
            save_frame_candidate(
                scan_task,
                job_id="test_job_001",
                workflow="PESsearch",
                view_type="scan",
                frame_index=0,
                role="TS",
            )

    def test_invalid_role_rejected(self, scan_task: Path) -> None:
        with pytest.raises(FrameCandidateError, match="role"):
            save_frame_candidate(
                scan_task,
                job_id="test_job_001",
                workflow="scan",
                view_type="scan",
                frame_index=0,
                role="INVALID",
            )

    def test_save_failure_writes_nothing(self, scan_task: Path) -> None:
        """FrameCandidateError on resolve should leave zero artifacts."""
        with pytest.raises(FrameCandidateError):
            save_frame_candidate(
                scan_task,
                job_id="test_job_001",
                workflow="scan",
                view_type="scan",
                frame_index=999,  # missing frame
                role="TS",
            )
        # No structures dir should exist
        structures_dir = scan_task / "RESULT" / "structures"
        assert not structures_dir.exists()
        # No authority file
        authority_path = scan_task / "RESULT" / FRAME_CANDIDATES_RELATIVE_PATH.split("/", 1)[1]
        assert not authority_path.exists()


# ---------------------------------------------------------------------------
# Revision conflict tests
# ---------------------------------------------------------------------------


class TestRevisionConflict:
    """expected_revision mismatch raises RevisionConflictError."""

    def test_revision_conflict_on_save(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        with pytest.raises(RevisionConflictError):
            save_frame_candidate(
                scan_task,
                job_id="test_job_001",
                workflow="scan",
                view_type="scan",
                frame_index=1,
                role="INT",
                expected_revision=0,  # stale — should be 1
            )

    def test_revision_conflict_on_remove(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        with pytest.raises(RevisionConflictError):
            remove_frame_candidate(scan_task, "scan_ts_frame_000", expected_revision=0)


# ---------------------------------------------------------------------------
# list_frame_candidates tests
# ---------------------------------------------------------------------------


class TestListFrameCandidates:
    """None-safe listing, empty when missing/corrupt."""

    def test_empty_when_no_authority(self, tmp_path: Path) -> None:
        result = list_frame_candidates(tmp_path)
        assert result["schema_version"] == FRAME_CANDIDATES_SCHEMA
        assert result["candidates"] == []
        assert result["revision"] == 0

    def test_list_after_save(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        result = list_frame_candidates(scan_task)
        assert len(result["candidates"]) == 1
        assert result["candidates"][0]["candidate_id"] == "scan_ts_frame_000"

    def test_corrupt_authority_returns_empty(self, scan_task: Path) -> None:
        authority_path = scan_task / "RESULT" / "frame_candidates.json"
        authority_path.parent.mkdir(parents=True, exist_ok=True)
        authority_path.write_text("NOT VALID JSON", encoding="utf-8")
        result = list_frame_candidates(scan_task)
        assert result["candidates"] == []
        assert result["revision"] == 0


# ---------------------------------------------------------------------------
# remove_frame_candidate tests
# ---------------------------------------------------------------------------


class TestRemoveFrameCandidate:
    """Remove unregisters but keeps xyz on disk."""

    def test_remove_keeps_file(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        xyz_path = scan_task / "RESULT" / "structures" / "scan_ts_frame_000.xyz"
        assert xyz_path.is_file()
        remove_frame_candidate(scan_task, "scan_ts_frame_000")
        # File kept
        assert xyz_path.is_file()
        # But removed from authority + manifest
        result = list_frame_candidates(scan_task)
        assert len(result["candidates"]) == 0

    def test_remove_increments_revision(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        result = remove_frame_candidate(scan_task, "scan_ts_frame_000")
        assert result["revision"] == 2

    def test_remove_unknown_candidate(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        with pytest.raises(FrameCandidateError, match="not found"):
            remove_frame_candidate(scan_task, "nonexistent_candidate")

    def test_remove_from_manifest(self, scan_task: Path) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        remove_frame_candidate(scan_task, "scan_ts_frame_000")
        manifest_path = scan_task / "RESULT" / "result_manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        frame_products = [
            p
            for p in manifest.get("products", [])
            if p.get("id", "").startswith("frame_candidate_")
        ]
        assert len(frame_products) == 0

    def test_remove_manifest_failure_restores_authority(
        self, scan_task: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        authority_path = scan_task / "RESULT" / FRAME_CANDIDATES_RELATIVE_PATH.split("/", 1)[1]
        authority_before = authority_path.read_bytes()

        def fail_manifest_write(_manifest: ResultManifest, _result_dir: Path | str) -> Path:
            raise OSError("manifest write failed")

        monkeypatch.setattr(ResultManifest, "write", fail_manifest_write)
        with pytest.raises(OSError, match="manifest write failed"):
            remove_frame_candidate(scan_task, "scan_ts_frame_000")

        assert authority_path.read_bytes() == authority_before
        assert len(list_frame_candidates(scan_task)["candidates"]) == 1


# ---------------------------------------------------------------------------
# Path escape tests
# ---------------------------------------------------------------------------


class TestPathEscape:
    """Resolved paths MUST stay under task_root."""

    def test_scan_path_escape_rejected(self, scan_task: Path) -> None:
        """A trajectory frame referencing ../../etc/passwd must be rejected."""
        traj_path = scan_task / "RESULT" / "trajectories" / "scan_trajectory.json"
        trajectory = json.loads(traj_path.read_text(encoding="utf-8"))
        trajectory["frames"].append(
            {"index": 99, "path": "../../etc/passwd", "energy_hartree": -100.0}
        )
        traj_path.write_text(json.dumps(trajectory, indent=2), encoding="utf-8")
        with pytest.raises(FrameCandidateError, match="escapes"):
            resolve_frame_geometry(scan_task, view_type="scan", frame_index=99, workflow="scan")

    def test_conformer_manifest_path_escape_rejected(self, conformer_task: Path) -> None:
        manifest_root = conformer_task / "manifest-root"
        manifest_root.mkdir()
        manifest_path = conformer_task / "RESULT" / "confsearch" / "confsearch_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["conformers"][0]["geometry"] = "../escape.xyz"
        (manifest_root / "confsearch_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        (manifest_root.parent / "escape.xyz").write_text(_SAMPLE_XYZ, encoding="utf-8")

        with pytest.raises(FrameCandidateError, match="escapes"):
            resolve_frame_geometry(
                manifest_root, view_type="conformer", frame_index=0, workflow="Confsearch"
            )


# ---------------------------------------------------------------------------
# Structure sources TAG parse integration
# ---------------------------------------------------------------------------


class TestStructureSourcesIntegration:
    """TAG line must be parseable by structure_sources patterns."""

    def test_tag_line_parseable_by_structure_sources(self, scan_task: Path) -> None:
        """Save a candidate and verify structure_sources regex can parse the TAG."""
        import re

        # These are the EXACT patterns from structure_sources.py
        tag_re = re.compile(r"\bTAG\s*[:=]\s*(TS|INT)\b", re.IGNORECASE)
        tag_id_re = re.compile(r"\bcandidate_id\s*=\s*([^\s|]+)", re.IGNORECASE)

        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        xyz_path = scan_task / "RESULT" / "structures" / "scan_ts_frame_000.xyz"
        xyz_text = xyz_path.read_text(encoding="utf-8")
        lines = xyz_text.strip().splitlines()
        # Second line is the TAG comment
        comment = lines[1]
        tag_match = tag_re.search(comment)
        id_match = tag_id_re.search(comment)
        assert tag_match is not None, f"TAG regex failed on: {comment}"
        assert tag_match.group(1).upper() == "TS"
        assert id_match is not None, f"candidate_id regex failed on: {comment}"
        assert id_match.group(1) == "scan_ts_frame_000"

    def test_tag_none_role_parsed_as_int(self, scan_task: Path) -> None:
        """NONE role produces TAG: INT which structure_sources sees."""
        import re

        tag_re = re.compile(r"\bTAG\s*[:=]\s*(TS|INT)\b", re.IGNORECASE)
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="NONE",
        )
        xyz_path = scan_task / "RESULT" / "structures" / "scan_none_frame_000.xyz"
        xyz_text = xyz_path.read_text(encoding="utf-8")
        comment = xyz_text.strip().splitlines()[1]
        tag_match = tag_re.search(comment)
        assert tag_match is not None
        assert tag_match.group(1).upper() == "INT"

    def test_manifest_metadata_for_structure_sources(self, scan_task: Path) -> None:
        """Product metadata should carry candidate_id and role for structure_sources."""
        save_frame_candidate(
            scan_task,
            job_id="test_job_001",
            workflow="scan",
            view_type="scan",
            frame_index=0,
            role="TS",
        )
        manifest_path = scan_task / "RESULT" / "result_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        products = manifest.get("products", [])
        frame_products = [p for p in products if p.get("id", "").startswith("frame_candidate_")]
        assert len(frame_products) == 1
        meta = frame_products[0]["metadata"]
        assert meta["candidate_id"] == "scan_ts_frame_000"
        assert meta["role"] == "TS"
        assert meta["selection_source"] == "manual_frame"
        assert meta["source"] == "scan"
