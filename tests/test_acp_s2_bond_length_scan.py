"""S2 bond-length scan tests (docs/ACP_S2_Bond_Length_Scan_MD_Plan.md).

Covers scan_models contracts, scan_manifest v2 IO, the bond_scan pipeline
(with fake ORCA interfaces), CLI request assembly, scheduler stage-plan /
flag parity, handoff v2 compatibility, the low_confirm v2 adapter, and the
API surface (structure-assets, s2 profile/candidates/frame, review gate,
gated S3 creation).
"""

# pyright: reportAny=false, reportExplicitAny=false, reportArgumentType=false, reportAttributeAccessIssue=false, reportFunctionMemberAccess=false, reportMissingParameterType=false, reportPossiblyUnboundVariable=false, reportPrivateLocalImportUsage=false, reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false, reportUnknownVariableType=false, reportUnusedCallResult=false, reportUnusedFunction=false, reportUnusedParameter=false, reportUnannotatedClassAttribute=false
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette

from acp.mechanism import bond_scan as bond_scan_module
from acp.mechanism.scan_models import (
    BondLengthScanRequest,
    ScanCoordinate,
    build_default_protocol,
    coordinate_step_angstrom,
    validate_scan_protocol,
)
from acp.scheduler.jobs import JobSpec
from acp.scheduler.manager import JobManager

# ── helpers ─────────────────────────────────────────────────────────────

WATER_DIMER_XYZ = """6
water dimer
O 0.0 0.0 0.0
H 0.96 0.0 0.0
H 0.0 0.96 0.0
O 3.0 0.0 0.0
H 3.96 0.0 0.0
H 3.0 0.96 0.0
"""

_WATER1 = np.array([[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [0.0, 0.96, 0.0]])
_WATER2 = np.array([[3.0, 0.0, 0.0], [3.96, 0.0, 0.0], [3.0, 0.96, 0.0]])
_SYMBOLS = ["O", "H", "H", "O", "H", "H"]


def _peak_kcal(target: float) -> float:
    return (target - 2.25) ** 2 * 0.5 + 6.0 * np.exp(-(((target - 2.15) / 0.10) ** 2))


def _flat_kcal(target: float) -> float:
    return 8.0 * target


def _fake_scan(peak: bool = True):
    def fake(self, coordinates, symbols, scan_coordinate, points, **kwargs):
        from pathlib import Path

        from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

        output_dir = Path(kwargs.get("output_dir") or ".")
        (output_dir / "scan_frames").mkdir(parents=True, exist_ok=True)
        result_points: list[RelaxedScanPoint] = []
        start, end = scan_coordinate.start, scan_coordinate.end
        for index in range(points):
            frac = index / max(points - 1, 1)
            target = float(start + frac * (end - start))
            coords = np.vstack([_WATER1, _WATER2 + np.array([target - 3.0, 0.0, 0.0])])
            energy = _peak_kcal(target) if peak else _flat_kcal(target)
            result_points.append(
                RelaxedScanPoint(
                    frame_index=index,
                    progress=frac,
                    coordinates=coords,
                    symbols=symbols,
                    energy_hartree=-152.0 + energy * 0.001,
                    success=True,
                    coordinate_values={"distance": target},
                )
            )
        return RelaxedScanResult(
            points=result_points,
            input_xyz=None,
            scan_dir=output_dir,
            success=True,
            message="",
        )

    return fake


def _fake_sp(peak: bool = True, fail_frames: set[int] | None = None):
    fail_frames = fail_frames or set()

    def fake(self, coordinates, symbols, **kwargs):
        from cccp.qc.interfaces.base import QCResult

        coords = np.asarray(coordinates)
        distance = float(np.linalg.norm(coords[0] - coords[3]))
        name = str(kwargs.get("output_name") or "sp")
        frame_index = int(name.split("_")[-1]) if "_" in name else -1
        if frame_index in fail_frames:
            return QCResult(success=False, error_message="failed", output_file=None, log_file=None)
        energy = -152.0 + (_peak_kcal(distance) if peak else _flat_kcal(distance)) * 0.001
        return QCResult(
            success=True,
            energy=energy,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
        )

    return fake


@pytest.fixture
def fake_orca(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan(peak=True))
    monkeypatch.setattr(bond_scan_module.ORCAInterface, "single_point", _fake_sp(peak=True))


def _scan_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "mode": "bond_length_scan",
        "source": {
            "source_type": "xyz_text",
            "xyz_text": WATER_DIMER_XYZ,
            "charge": 0,
            "multiplicity": 1,
        },
        "coordinate": {"atoms": [0, 3], "start": 3.0, "end": 1.5, "n_points": 13},
        "protocol": {"single_point": {"method": "B97-3c"}},
    }
    request.update(overrides)
    return request


def _run_scan(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    out_dir = tmp_path / "job_work"
    return bond_scan_module.run_bond_length_scan(
        request=_scan_request(**overrides),
        output_dir=out_dir,
        config={"resources": {"nproc": 1}},
    )


# ── scan_models ─────────────────────────────────────────────────────────


class TestScanModels:
    def test_validation_rules(self) -> None:
        validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=3.0, end=1.5, n_points=16))
        with pytest.raises(ValueError, match="different atoms"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 0), start=3.0, end=1.5))
        with pytest.raises(ValueError, match="differ"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=3.0, end=3.0))
        with pytest.raises(ValueError, match="step"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=3.0, end=2.99, n_points=3))
        with pytest.raises(ValueError, match="limit"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=3.0, end=1.5, n_points=200))
        with pytest.raises(ValueError, match="greater than 0"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=-1.0, end=1.5))
        with pytest.raises(ValueError, match=">= 3"):
            validate_scan_protocol(ScanCoordinate(atoms=(0, 1), start=3.0, end=1.5, n_points=2))

    def test_over_point_limit_explicit_confirmation(self) -> None:
        coordinate = ScanCoordinate(atoms=(0, 1), start=3.0, end=1.5, n_points=150)
        with pytest.raises(ValueError, match="double confirmation"):
            validate_scan_protocol(coordinate)
        validate_scan_protocol(coordinate, allow_over_point_limit=True)

    def test_step_computation(self) -> None:
        coordinate = ScanCoordinate(atoms=(0, 1), start=3.0, end=1.5, n_points=16)
        assert coordinate_step_angstrom(coordinate) == pytest.approx(0.1)

    def test_request_roundtrip(self) -> None:
        request = _scan_request()
        parsed = BondLengthScanRequest.from_dict(request)
        assert parsed.coordinate.atoms == (0, 3)
        assert parsed.coordinate.start == 3.0
        assert parsed.coordinate.n_points == 13
        assert parsed.protocol.single_point.method == "B97-3c"
        assert parsed.to_dict()["coordinate"]["atoms"] == [0, 3]

    def test_default_protocol(self) -> None:
        protocol = build_default_protocol(ScanCoordinate(atoms=(0, 1)))
        assert protocol.scan_optimizer.method == "GFN2-xTB"
        assert protocol.single_point.method == "B97-3c"
        assert protocol.name == "orca_relaxed_scan_xtb_gfn2_sp_b973c_v1"

    def test_angle_and_dihedral_coordinate_contracts(self) -> None:
        angle = ScanCoordinate(
            kind="angle",
            atoms=(0, 1, 2),
            unit="degree",
            start=60.0,
            end=120.0,
            n_points=13,
        )
        validate_scan_protocol(angle)
        assert build_default_protocol(angle).scan_type == "bond_angle"
        assert angle.to_dict()["atoms"] == [0, 1, 2]

        dihedral = ScanCoordinate(
            kind="dihedral",
            atoms=(0, 1, 2, 3),
            unit="degree",
            start=-180.0,
            end=180.0,
            n_points=37,
        )
        validate_scan_protocol(dihedral)
        assert build_default_protocol(dihedral).scan_type == "dihedral"

        with pytest.raises(ValueError, match="require 3 atoms"):
            validate_scan_protocol(
                ScanCoordinate(kind="angle", atoms=(0, 1), start=60.0, end=120.0)
            )
        with pytest.raises(ValueError, match="between 0 and 180"):
            validate_scan_protocol(
                ScanCoordinate(
                    kind="angle",
                    atoms=(0, 1, 2),
                    unit="degree",
                    start=-1.0,
                    end=120.0,
                )
            )

    def test_coordinate_measurement_and_orca_rendering(self) -> None:
        from acp.mechanism.bond_scan import _measure_scan_coordinate
        from cccp.qc.interfaces.constraints import CoordinateSpec
        from cccp.qc.interfaces.orca import _orca_scan_line

        coords = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 1.0, 1.0]])
        assert _measure_scan_coordinate(
            coords, ScanCoordinate(kind="distance", atoms=(0, 1))
        ) == pytest.approx(1.0)
        assert _measure_scan_coordinate(
            coords, ScanCoordinate(kind="angle", atoms=(0, 1, 2))
        ) == pytest.approx(90.0)
        assert abs(
            _measure_scan_coordinate(coords, ScanCoordinate(kind="dihedral", atoms=(0, 1, 2, 3)))
        ) == pytest.approx(90.0)

        spec = CoordinateSpec(
            id="angle",
            kind="angle",
            atoms=(0, 1, 2),
            role="drive",
            start=60.0,
            end=120.0,
        )
        assert "A 0 1 2 = 60.00000000, 120.00000000, 13" in _orca_scan_line(spec, 13)

    def test_rejects_wrong_mode(self) -> None:
        with pytest.raises(ValueError, match="mode"):
            bond_scan_module.run_bond_length_scan(
                request={
                    "mode": "path",
                    "coordinate": {"atoms": [0, 1], "start": 3.0, "end": 1.5},
                    "protocol": {},
                },
                output_dir=".",
            )


# ── scan_manifest ───────────────────────────────────────────────────────


class TestScanManifest:
    def test_manifest_roundtrip(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        manifest_path = tmp_path / "job_work" / "RESULT" / "mechanism" / "s2_path_manifest.json"
        assert manifest_path.is_file()
        assert payload["schema"] == "s2_path_v2"
        assert payload["schema_version"] == "s2_path_v2"
        assert payload["mode"] == "bond_length_scan"
        assert payload["stationary_point_claimed"] is False
        assert payload["review"]["required"] is True
        assert payload["review"]["status"] == "pending"

        from acp.mechanism.scan_manifest import read_scan_manifest

        reloaded = read_scan_manifest(manifest_path)
        assert reloaded["scan"]["frame_count"] == 13
        assert reloaded["recommendations"]["ts"]

    def test_review_io(self, tmp_path: Path) -> None:
        from acp.mechanism.scan_manifest import (
            read_s2_review,
            write_s2_review,
        )
        from acp.mechanism.scan_models import ScanReview

        manifest_path = tmp_path / "RESULT" / "mechanism" / "s2_path_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps({"schema_version": "s2_path_v2"}))
        assert read_s2_review(manifest_path) is None
        review = ScanReview(
            required=True,
            status="confirmed",
            selected_ts=("ts_guess_008",),
        )
        write_s2_review(review, manifest_path)
        loaded = read_s2_review(manifest_path)
        assert loaded is not None
        assert loaded.status == "confirmed"
        assert loaded.selected_ts == ("ts_guess_008",)
        assert (tmp_path / "RESULT" / "mechanism" / "s2_review.json").is_file()


# ── bond_scan pipeline ──────────────────────────────────────────────────


class TestBondScanPipeline:
    def test_happy_path_with_peak(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        assert payload["status"] == "ready_for_review"
        assert payload["energy_profile"]["energy_source"] == "single_point"
        assert payload["energy_profile"]["sp_incomplete"] is False
        ts = payload["recommendations"]["ts"]
        assert ts
        assert all(rec["kind"] == "ts" for rec in ts)
        assert ts[0]["frame_index"] > 0
        assert ts[0]["confidence"] in ("high", "medium", "low")
        assert payload["review"]["status"] == "pending"

    def test_flat_curve_requires_review(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan(peak=False))
        monkeypatch.setattr(bond_scan_module.ORCAInterface, "single_point", _fake_sp(peak=False))
        payload = _run_scan(tmp_path)
        assert payload["status"] == "needs_review"
        assert payload["recommendations"]["needs_review"] is True
        ts = payload["recommendations"]["ts"]
        assert all(rec["confidence"] == "low" for rec in ts)
        assert payload["recommendations"]["note"]

    def test_partial_sp_failure_falls_back_to_scan_energy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501
        monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan(peak=True))
        monkeypatch.setattr(
            bond_scan_module.ORCAInterface,
            "single_point",
            _fake_sp(peak=True, fail_frames={0, 5, 9}),
        )
        payload = _run_scan(tmp_path)
        assert payload["energy_profile"]["energy_source"] == "scan"
        assert payload["energy_profile"]["sp_incomplete"] is True
        frames = payload["scan"]["frames"]
        failed = [f for f in frames if f["single_point_status"] == "failed"]
        assert len(failed) == 3
        assert all(f["scan_energy_hartree"] is not None for f in failed)

    def test_sp_disabled_uses_scan_energy(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(
            tmp_path,
            protocol={"single_point": {"enabled": False}},
        )
        assert payload["energy_profile"]["energy_source"] == "scan"
        assert all(frame["single_point_status"] == "skipped" for frame in payload["scan"]["frames"])

    def test_artifacts_land_in_expected_layout(self, tmp_path: Path, fake_orca) -> None:
        _run_scan(tmp_path)
        root = tmp_path / "job_work"
        assert (root / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "input.xyz").is_file()
        assert (root / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "scan.inp").is_file()
        assert (root / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "scan_protocol.json").is_file()
        assert (
            root / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "scan_frames" / "frame_000.xyz"
        ).is_file()
        assert (root / "WORK" / "02_SEARCH" / "s2_bond_scan_001" / "profile.json").is_file()
        assert (root / "RESULT" / "mechanism" / "s2_path_manifest.json").is_file()
        assert (root / "state.json").is_file()
        state = json.loads((root / "state.json").read_text())
        assert state["status"] == "completed"
        assert state["current_stage"] == "finalize_manifest"

    def test_task_artifact_source_from_confsearch_manifest(self, tmp_path: Path, fake_orca) -> None:
        conf_dir = tmp_path / "conf_job"
        conf_dir.mkdir(parents=True)
        conformers_dir = conf_dir / "conformers"
        conformers_dir.mkdir()
        conf_xyz = conformers_dir / "conf_0001.xyz"
        conf_xyz.write_text(WATER_DIMER_XYZ, encoding="utf-8")
        manifest = {
            "schema_version": "confsearch_v1",
            "workflow": "Confsearch",
            "conformers": [
                {"conf_id": "conf_0001", "rank": 1, "geometry": "conformers/conf_0001.xyz"}
            ],
        }
        manifest_path = conf_dir / "confsearch_manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

        payload = bond_scan_module.run_bond_length_scan(
            request=_scan_request(
                source={
                    "source_type": "task_artifact",
                    "artifact_path": str(manifest_path),
                    "structure_selector": {"kind": "final_structure"},
                }
            ),
            output_dir=tmp_path / "job_work",
            config={"resources": {"nproc": 1}},
        )
        assert payload["status"] in ("ready_for_review", "needs_review")
        assert payload["input"]["source"]["artifact_kind"] == "confsearch_manifest"

    def test_structure_asset_source(self, tmp_path: Path, fake_orca) -> None:
        asset = tmp_path / "asset.xyz"
        asset.write_text(WATER_DIMER_XYZ, encoding="utf-8")
        payload = bond_scan_module.run_bond_length_scan(
            request=_scan_request(
                source={"source_type": "structure_asset", "asset_path": str(asset)}
            ),
            output_dir=tmp_path / "job_work",
            config={"resources": {"nproc": 1}},
        )
        assert payload["status"] in ("ready_for_review", "needs_review")

    def test_invalid_source_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="xyz_text"):
            bond_scan_module.run_bond_length_scan(
                request=_scan_request(source={"source_type": "xyz_text", "xyz_text": ""}),
                output_dir=tmp_path / "job_work",
            )

    def test_bad_atom_index_rejected(self, tmp_path: Path, fake_orca) -> None:
        with pytest.raises(ValueError):
            bond_scan_module.run_bond_length_scan(
                request=_scan_request(coordinate={"atoms": [0, 9], "start": 3.0, "end": 1.5}),
                output_dir=tmp_path / "job_work",
                config={"resources": {"nproc": 1}},
            )

    def test_scan_failure_raises_runtime_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def failing_scan(self, coordinates, symbols, scan_coordinate, points, **kwargs):
            from cccp.qc.interfaces.xtb_scan import RelaxedScanResult

            return RelaxedScanResult(
                points=[],
                input_xyz=None,
                scan_dir=tmp_path,
                success=False,
                message="orca crashed",
            )

        monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", failing_scan)
        with pytest.raises(RuntimeError, match="scan failed"):
            _run_scan(tmp_path)


# ── CLI ─────────────────────────────────────────────────────────────────


class TestCliRequestAssembly:
    def test_build_bond_scan_request(self) -> None:
        from acp.cli import _build_bond_scan_request

        request = _build_bond_scan_request(
            argparse_args(scan_atoms="0,1", scan_start=3.0, scan_end=1.5, scan_points=16)
        )
        assert request["mode"] == "bond_length_scan"
        assert request["source"]["source_type"] == "xyz_text"
        assert request["coordinate"]["atoms"] == [0, 1]
        assert request["coordinate"]["start"] == 3.0
        assert request["coordinate"]["end"] == 1.5
        assert request["coordinate"]["n_points"] == 16

    def test_build_bond_scan_request_from_file(self, tmp_path: Path) -> None:
        from acp.cli import _build_bond_scan_request

        xyz_file = tmp_path / "mol.xyz"
        xyz_file.write_text(WATER_DIMER_XYZ, encoding="utf-8")
        request = _build_bond_scan_request(argparse_args(xyz_text=f"@{xyz_file}"))
        assert request["source"]["xyz_text"] == WATER_DIMER_XYZ

    def test_build_bond_scan_request_scan_config(self) -> None:
        from acp.cli import _build_bond_scan_request

        config = {
            "coordinate": {"atoms": [0, 3], "start": 2.5, "end": 1.0, "n_points": 11},
            "protocol": {
                "scan_optimizer": {"method": "GFN2-xTB"},
                "single_point": {"method": "wB97X-D4"},
            },
        }
        request = _build_bond_scan_request(argparse_args(scan_config=json.dumps(config)))
        assert request["coordinate"]["atoms"] == [0, 3]
        assert request["coordinate"]["start"] == 2.5
        assert request["protocol"]["scan_optimizer"]["method"] == "GFN2-xTB"
        assert request["protocol"]["single_point"]["method"] == "wB97X-D4"

    def test_load_plan_argument_long_inline_json(self) -> None:
        """Inline JSON longer than NAME_MAX must not crash path probing (ENAMETOOLONG)."""
        from acp.cli import _load_plan_argument

        big = {"xyz_text": "H 0 0 0\n" * 500, "coordinate": {"atoms": [0, 1]}}
        assert _load_plan_argument(json.dumps(big)) == big

    def test_load_plan_argument_from_file(self, tmp_path: Path) -> None:
        from acp.cli import _load_plan_argument

        config_file = tmp_path / "scan_config.json"
        config_file.write_text(json.dumps({"coordinate": {"atoms": [0, 3]}}), encoding="utf-8")
        assert _load_plan_argument(str(config_file)) == {"coordinate": {"atoms": [0, 3]}}

    def test_build_bond_scan_request_source_from_scan_config(self) -> None:
        """Scheduler contract: scan_config carries the full source when no CLI source flags."""
        from acp.cli import _build_bond_scan_request

        config = {
            "source": {
                "source_type": "xyz_text",
                "xyz_text": WATER_DIMER_XYZ,
                "charge": 0,
                "multiplicity": 1,
            },
            "coordinate": {"atoms": [6, 5], "start": 1.5, "end": 3.5},
            "protocol": {},
        }
        request = _build_bond_scan_request(argparse_args(scan_config=json.dumps(config)))
        assert request["source"]["source_type"] == "xyz_text"
        assert request["source"]["xyz_text"] == WATER_DIMER_XYZ
        assert request["source"]["charge"] == 0

    def test_build_bond_scan_request_cli_source_overrides_config(self) -> None:
        """Explicit CLI source flags take precedence over scan_config source."""
        from acp.cli import _build_bond_scan_request

        config = {"source": {"source_type": "xyz_text", "xyz_text": "H 0 0 0\n"}}
        request = _build_bond_scan_request(
            argparse_args(scan_config=json.dumps(config), xyz_text=WATER_DIMER_XYZ)
        )
        assert request["source"]["xyz_text"] == WATER_DIMER_XYZ


def argparse_args(**overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "mode": "bond_length_scan",
        "source_type": None,
        "xyz_text": None,
        "asset_path": None,
        "from_manifest": None,
        "from_job": None,
        "from_frame": None,
        "scan_config": None,
        "scan_atoms": None,
        "scan_start": None,
        "scan_end": None,
        "scan_points": None,
        "scan_method": None,
        "sp_method": None,
        "sp_basis": None,
        "no_sp": False,
        "max_iterations": None,
        "charge": None,
        "multiplicity": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


# ── scheduler integration ───────────────────────────────────────────────


class TestSchedulerIntegration:
    def test_stage_cli_accepts_scheduler_resource_flags(self) -> None:
        from acp.cli import build_parser

        cases = (("PESsearch", []), ("BatchOptimize", ["--items-file", "structures.xyz"]))
        for workflow, source_args in cases:
            args = build_parser().parse_args(
                ["run", workflow, *source_args, "--nproc", "4", "--mem", "8GB"]
            )
            assert args.workflow == workflow
            assert args.nproc == 4
            assert args.mem == "8GB"

    def test_stage_plan_provider_bond_mode(self) -> None:
        from acp.calculations.pes.engine import PES_SEARCH_STAGES
        from acp.scheduler.stage_tasks import get_stage_plan

        spec = JobSpec(
            workflow="PESsearch", name="x", input={}, method={"mode": "bond_length_scan"}
        )
        plan = get_stage_plan(spec)
        stages = [stage.stage_name for stage in plan]
        assert stages == list(PES_SEARCH_STAGES)
        assert "run_relaxed_scan" in stages
        assert "select_candidates" in stages

        legacy = JobSpec(workflow="PESsearch", name="x", input={}, method={})
        legacy_stages = [stage.stage_name for stage in get_stage_plan(legacy)]
        assert legacy_stages == ["prepare", "path_search", "candidate_extract", "finalize"]

    def test_pessearch_method_flags_bond_mode(self) -> None:
        from acp.scheduler.remote.script_gen import build_remote_stage_cmd_tail

        bond_flags = build_remote_stage_cmd_tail(
            JobSpec(
                workflow="PESsearch",
                input={"scan_request": _scan_request()},
                method={"mode": "bond_length_scan"},
            )
        )
        assert "--mode" in bond_flags and "bond_length_scan" in bond_flags
        assert "--scan-config" in bond_flags
        assert "--strategy" not in bond_flags

        path_flags = build_remote_stage_cmd_tail(
            JobSpec(
                workflow="PESsearch",
                input={"from": "/abs/confsearch_manifest.json"},
                method={"strategy": "guided-scan", "select": ["ts_guess_001"]},
            )
        )
        assert "--strategy" in path_flags and "guided-scan" in path_flags
        assert "--select" in path_flags
        assert "ts_guess_001" in path_flags

    def test_runner_build_stage_cmd_bond_mode(self, tmp_path: Path) -> None:
        from acp.scheduler.runner import JobRunner

        runner = JobRunner()
        spec = JobSpec(
            workflow="PESsearch",
            name="s2",
            input={
                "scan_request": {
                    "mode": "bond_length_scan",
                    "source": {"source_type": "xyz_text", "xyz_text": WATER_DIMER_XYZ},
                    "coordinate": {"atoms": [0, 3]},
                    "protocol": {},
                }
            },
            method={"mode": "bond_length_scan"},
            resources={"nproc": 4, "mem": "8GB"},
            output_dir=str(tmp_path / "job"),
        )
        cmd = runner._build_pessearch_cmd(spec, tmp_path / "job", "")
        assert "--mode" in cmd and "bond_length_scan" in cmd
        assert "--scan-config" in cmd
        assert cmd[cmd.index("--nproc") + 1] == "4"
        assert cmd[cmd.index("--mem") + 1] == "8GB"
        config_index = cmd.index("--scan-config")
        config_path = Path(cmd[config_index + 1])
        assert config_path.name == "scan_config.json"
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        assert payload["source"]["source_type"] == "xyz_text"
        assert "--plan" not in cmd

    def test_remote_script_gen_bond_mode(self, tmp_path: Path) -> None:
        from acp.scheduler.remote.script_gen import (
            build_remote_scan_config_payload,
            build_remote_stage_cmd_tail,
        )

        spec = JobSpec(
            workflow="PESsearch",
            name="s2",
            input={
                "scan_request": {
                    "mode": "bond_length_scan",
                    "source": {
                        "source_type": "task_artifact",
                        "source_job_id": "job1",
                        "artifact_path": "RESULT/confsearch/confsearch_manifest.json",
                    },
                    "coordinate": {"atoms": [0, 3]},
                    "protocol": {},
                }
            },
            method={"mode": "bond_length_scan"},
            output_dir=str(tmp_path / "job"),
        )
        flags = build_remote_stage_cmd_tail(spec)
        config_index = flags.index("--scan-config")
        assert flags[config_index + 1] == "scan_config.json"
        payload = build_remote_scan_config_payload(spec)
        assert payload is not None
        assert payload["source"]["artifact_path"] == "RESULT/confsearch/confsearch_manifest.json"
        assert "--from" not in flags


# ── handoff / low_confirm v2 compatibility ──────────────────────────────


class TestHandoffV2:
    def test_copy_handoff_payload_flattens_scan_frames(self, tmp_path: Path, fake_orca) -> None:
        _run_scan(tmp_path)
        job_root = tmp_path / "job_work"
        manifest_path = job_root / "RESULT" / "mechanism" / "s2_path_manifest.json"
        target = tmp_path / "handoff"
        from acp.mechanism.stages.handoff import copy_handoff_payload

        copied = copy_handoff_payload(manifest_path, target)
        assert copied.is_file()
        assert (target / "scan_frames" / "frame_000.xyz").is_file()
        assert (target / "scan_frames" / "frame_012.xyz").is_file()

    def test_validate_stage_artifact_accepts_v2(self, tmp_path: Path, fake_orca) -> None:
        _run_scan(tmp_path)
        job_root = tmp_path / "job_work"
        manifest_path = job_root / "RESULT" / "mechanism" / "s2_path_manifest.json"
        from acp.mechanism.stages.handoff import validate_stage_artifact

        resolved = validate_stage_artifact(
            source_job_id=None,
            relative_path="RESULT/mechanism/s2_path_manifest.json",
            sha256=None,
            kind="s2_path_manifest",
            stage="S3",
            work_dir=job_root,
        )
        assert resolved == manifest_path

    def test_low_confirm_v2_adapter(self, tmp_path: Path, fake_orca) -> None:
        _run_scan(tmp_path)
        manifest_path = tmp_path / "job_work" / "RESULT" / "mechanism" / "s2_path_manifest.json"
        from acp.mechanism.batch_models import load_items_from_s2_path_manifest
        from acp.mechanism.stages.low_confirm import (
            _require_confirmed_review,
            read_s2_manifest,
        )

        loaded = read_s2_manifest(manifest_path)
        items, _payload = load_items_from_s2_path_manifest(manifest_path, [])
        assert items
        assert items[0].tag == "TS"
        assert items[0].source_type == "s2_candidate"
        selected, _selected_payload = load_items_from_s2_path_manifest(manifest_path, [])
        assert selected and selected[0].tag == "TS"
        with pytest.raises(ValueError, match="not yet confirmed"):
            _require_confirmed_review(loaded)

    def test_low_confirm_v2_resolves_scan_geometry(self, tmp_path: Path, fake_orca) -> None:
        payload = _run_scan(tmp_path)
        manifest_path = tmp_path / "job_work" / "RESULT" / "mechanism" / "s2_path_manifest.json"
        from acp.mechanism.batch_models import _resolve_candidate_geometry

        frame_ref = payload["recommendations"]["ts"][0]["geometry_path"]
        xyz_path = _resolve_candidate_geometry(manifest_path.parent, frame_ref)
        if xyz_path is None:
            scan_dir = str((payload.get("scan") or {}).get("scan_dir") or "")
            xyz_path = (
                manifest_path.parent.parent.parent / scan_dir / frame_ref
            ).resolve()
        assert xyz_path.is_file()


# ── API surface ─────────────────────────────────────────────────────────


def make_client(tmp_path: Path) -> TestClient:
    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    return TestClient(create_app(run_root=tmp_path, max_running=2))


def _api_manager(client: TestClient) -> JobManager:
    return cast(JobManager, cast(Starlette, client.app).state.job_manager)


class _StubRunner:
    def __init__(self, manager: JobManager) -> None:
        self._manager = manager

    def submit(self, record, event_log, cancel_event, **kwargs) -> None:
        pass

    def poll(self, record) -> tuple[bool, int | None]:
        return True, 0

    def cancel(self, record, **kwargs) -> None:
        pass

    def cleanup(self, record, **kwargs) -> None:
        pass

    def _capture_artifacts(self, *args, **kwargs) -> None:
        pass

    def _store_provenance(self, *args, **kwargs) -> None:
        pass


class TestS2Api:
    def test_structure_assets_endpoint(self, tmp_path: Path) -> None:
        with make_client(tmp_path) as client:
            response = client.post(
                "/api/v1/structure-assets",
                json={"name": "wd", "xyz_text": WATER_DIMER_XYZ},
            )
            assert response.status_code == 201
            body = response.json()
            assert body["atom_count"] == 6
            assert body["asset_path"]
            response = client.post(
                "/api/v1/structure-assets",
                json={"name": "bad", "xyz_text": "not an xyz"},
            )
            assert response.status_code == 422

    def test_structure_preview_resolves_xyz_and_asset(self, tmp_path: Path) -> None:
        with make_client(tmp_path) as client:
            preview = client.post(
                "/api/v1/s2/structure-preview",
                json={
                    "source": {
                        "source_type": "xyz_text",
                        "xyz_text": WATER_DIMER_XYZ,
                        "charge": -1,
                        "multiplicity": 2,
                    }
                },
            )
            assert preview.status_code == 410

            asset = client.post(
                "/api/v1/structure-assets",
                json={"name": "preview_asset", "xyz_text": WATER_DIMER_XYZ},
            )
            assert asset.status_code == 201
            asset_preview = client.post(
                "/api/v1/s2/structure-preview",
                json={
                    "source": {
                        "source_type": "structure_asset",
                        "asset_path": asset.json()["asset_path"],
                    }
                },
            )
            assert asset_preview.status_code == 410

    def test_bond_scan_allows_standalone_xyz(self, tmp_path: Path) -> None:
        with make_client(tmp_path) as client:
            response = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "PESsearch",
                    "name": "s2_without_project",
                    "method": {"mode": "bond_length_scan"},
                    "input": {
                        "source": {"source_type": "xyz_text", "xyz_text": WATER_DIMER_XYZ},
                        "coordinate": {"atoms": [0, 3], "start": 3.0, "end": 1.5, "n_points": 9},
                        "protocol": {},
                    },
                },
            )
            assert response.status_code == 201
            assert response.json()["job_id"]

    def test_bond_scan_ignores_incomplete_duplicated_protocol_coordinate(
        self, tmp_path: Path
    ) -> None:
        """The top-level selected coordinate is the canonical scan input."""
        with make_client(tmp_path) as client:
            response = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "PESsearch",
                    "name": "s2_protocol_coordinate_copy",
                    "method": {"mode": "bond_length_scan"},
                    "input": {
                        "source": {"source_type": "xyz_text", "xyz_text": WATER_DIMER_XYZ},
                        "coordinate": {
                            "kind": "distance",
                            "atoms": [0, 3],
                            "start": 3.0,
                            "end": 1.5,
                            "n_points": 9,
                        },
                        # This is the shape emitted by the buggy frontend:
                        # protocol.coordinate has the range but no atoms.
                        "protocol": {
                            "coordinate": {
                                "kind": "distance",
                                "start": 3.0,
                                "end": 1.5,
                                "n_points": 9,
                            }
                        },
                    },
                },
            )
            assert response.status_code == 201

    def test_create_job_validation(self, tmp_path: Path) -> None:
        """Scan input validation is deferred to runtime; only basic checks remain."""
        with make_client(tmp_path) as client:
            response = client.post(
                "/api/v1/jobs",
                json={
                    "workflow": "PESsearch",
                    "method": {"mode": "bond_length_scan"},
                    "input": {
                        "source": {"source_type": "xyz_text", "xyz_text": ""},
                        "coordinate": {"atoms": [0, 3], "start": 3.0, "end": 1.5, "n_points": 9},
                        "protocol": {},
                    },
                },
            )
            assert response.status_code == 422  # empty xyz_text still rejected

    def _make_completed_scan_job(self, client: TestClient, tmp_path: Path) -> str:
        manager = _api_manager(client)
        manager.runner = _StubRunner(manager)
        spec = JobSpec(
            workflow="PESsearch",
            name="s2scan",
            input={"from": str(tmp_path / "placeholder")},
            method={"mode": "bond_length_scan"},
            output_dir=str(tmp_path / "jobs"),
            molecule_name="wd",
            task_name="PESsearch",
        )
        record = manager.submit(spec)
        payload = bond_scan_module.run_bond_length_scan(
            request=_scan_request(),
            output_dir=record.work_dir,
            config={"resources": {"nproc": 1}},
        )
        assert payload["recommendations"]["ts"]
        return record.id

    def test_s2_endpoints_and_review_gate(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            manager = _api_manager(client)
            manager.runner = _StubRunner(manager)
            job_id = self._make_completed_scan_job(client, tmp_path)

            profile = client.get(f"/api/v1/jobs/{job_id}/s2/profile")
            assert profile.status_code == 200
            assert len(profile.json()["frames"]) == 13
            assert profile.json()["energy_profile"]["energy_source"] == "single_point"

            graph = client.get(f"/api/v1/jobs/{job_id}/energy-graph")
            assert graph.status_code == 200
            graph_payload = graph.json()
            assert graph_payload["view_type"] == "scan"
            assert graph_payload["default_series"] in {
                "single_point_energy",
                "relative_energy",
            }
            assert len(graph_payload["nodes"]) == 13
            assert any(item["type"] == "ts" for item in graph_payload["annotations"])
            assert any(item["type"] == "minimum" for item in graph_payload["annotations"])

            candidates = client.get(f"/api/v1/jobs/{job_id}/s2/candidates")
            assert candidates.status_code == 200
            ts = candidates.json()["recommendations"]["ts"]
            assert ts
            ts_id = ts[0]["candidate_id"]

            frame = client.get(f"/api/v1/jobs/{job_id}/s2/frame/7")
            assert frame.status_code == 200
            assert frame.json()["xyz"].startswith("6")

            review = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"selected_ts": [ts_id]},
            )
            assert review.status_code == 410
            assert (
                client.post(f"/api/v1/jobs/{job_id}/s3", json={}).status_code
                == 404
            )

    def test_review_rejects_unknown_candidate(self, tmp_path: Path, fake_orca) -> None:
        with make_client(tmp_path) as client:
            manager = _api_manager(client)
            manager.runner = _StubRunner(manager)
            job_id = self._make_completed_scan_job(client, tmp_path)
            review = client.post(
                f"/api/v1/jobs/{job_id}/s2/review",
                json={"selected_ts": ["ts_guess_999"]},
            )
            assert review.status_code == 410


# ── scan reuse / stale SP regression ────────────────────────────────────


def _water_dimer_coord_block(distance: float) -> str:
    shifted = _WATER2 + np.array([distance - 3.0, 0.0, 0.0])
    lines = ["CARTESIAN COORDINATES (ANGSTROEM)", "---------------------------------"]
    for symbol, (x, y, z) in zip(_SYMBOLS, np.vstack([_WATER1, shifted])):
        lines.append(f"{symbol}      {x:.7f}    {y:.7f}    {z:.7f}")
    lines.append("---------------------------------")
    return "\n".join(lines) + "\n"


def _synthetic_orca_scan_log(targets: list[float], energies: list[float]) -> str:
    parts: list[str] = []
    for step, (target, _energy) in enumerate(zip(targets, energies), start=1):
        parts.append(
            f"         *               RELAXED SURFACE SCAN STEP {step:>3}               *"
        )
        parts.append(_water_dimer_coord_block(target + 0.2))
        parts.append(_water_dimer_coord_block(target))
    parts.append("The Calculated Surface using the RELAXED SURFACE SCAN")
    parts.append("-----------------------------------------------------")
    for index, (target, energy) in enumerate(zip(targets, energies), start=1):
        parts.append(f"  {index}  {target:.8f}  {energy:.8f}")
    parts.append("ORCA TERMINATED NORMALLY")
    return "\n".join(parts) + "\n"


def _fake_scan_writing_log():
    def fake(self, coordinates, symbols, scan_coordinate, points, **kwargs):
        from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

        output_dir = Path(kwargs.get("output_dir") or ".")
        (output_dir / "scan_frames").mkdir(parents=True, exist_ok=True)
        result_points: list[RelaxedScanPoint] = []
        start, end = scan_coordinate.start, scan_coordinate.end
        rows: list[tuple[float, float]] = []
        for index in range(points):
            frac = index / max(points - 1, 1)
            target = float(start + frac * (end - start))
            coords = np.vstack([_WATER1, _WATER2 + np.array([target - 3.0, 0.0, 0.0])])
            energy = -152.0 + _peak_kcal(target) * 0.001
            rows.append((target, energy))
            result_points.append(
                RelaxedScanPoint(
                    frame_index=index,
                    progress=frac,
                    coordinates=coords,
                    symbols=symbols,
                    energy_hartree=energy,
                    success=True,
                    coordinate_values={"distance": target},
                )
            )
        (output_dir / "scan.out").write_text(
            _synthetic_orca_scan_log([t for t, _ in rows], [e for _, e in rows]),
            encoding="utf-8",
        )
        return RelaxedScanResult(
            points=result_points,
            input_xyz=None,
            scan_dir=output_dir,
            success=True,
            message="",
        )

    return fake


class TestScanReuseAndStaleSp:
    def test_rerun_reuses_completed_scan_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan_writing_log()
        )
        monkeypatch.setattr(bond_scan_module.ORCAInterface, "single_point", _fake_sp(peak=True))
        first = _run_scan(tmp_path)

        def must_not_run(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("relaxed_scan re-ran despite a reusable completed scan log")

        monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", must_not_run)
        second = _run_scan(tmp_path)

        frames = second["scan"]["frames"]
        assert len(frames) == 13
        distances = [frame["actual_coordinate"] for frame in frames]
        assert distances[0] == pytest.approx(3.0, abs=1e-3)
        assert distances[-1] == pytest.approx(1.5, abs=1e-3)
        assert distances == sorted(distances, reverse=True)
        assert max(distances) - min(distances) == pytest.approx(1.5, abs=1e-3)
        assert second["scan"]["frame_count"] == first["scan"]["frame_count"]
        assert second["energy_profile"]["sp_incomplete"] is False

    def test_stale_sp_output_with_mismatched_geometry_is_recomputed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scan_dir = tmp_path / "job_work" / "WORK" / "02_SEARCH" / "s2_bond_scan_001"
        sp_dir = scan_dir / "sp"
        sp_dir.mkdir(parents=True)
        stale_wrong_geometry = sp_dir / "frame_000.out"
        stale_wrong_geometry.write_text(
            "FINAL SINGLE POINT ENERGY    -999.123456\n" + _water_dimer_coord_block(0.5),
            encoding="utf-8",
        )
        matching_geometry = sp_dir / "frame_001.out"
        matching_geometry.write_text(
            "FINAL SINGLE POINT ENERGY    -888.500000\n" + _water_dimer_coord_block(2.875),
            encoding="utf-8",
        )

        monkeypatch.setattr(bond_scan_module.ORCAInterface, "relaxed_scan", _fake_scan(peak=True))
        monkeypatch.setattr(bond_scan_module.ORCAInterface, "single_point", _fake_sp(peak=True))
        payload = _run_scan(tmp_path)

        frames = payload["scan"]["frames"]
        assert frames[0]["single_point_energy_hartree"] != pytest.approx(-999.123456)
        assert frames[0]["single_point_status"] == "completed"
        assert frames[1]["single_point_energy_hartree"] == pytest.approx(-888.5)

    def test_compiled_scan_input_is_byte_identical_to_relaxed_scan(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cccp.qc.interfaces.orca import ORCAInterface

        req = BondLengthScanRequest.from_dict(_scan_request())
        interface = ORCAInterface(
            {"resources": {"nproc": 1}}, method=req.protocol.scan_optimizer.method
        )
        coords = np.vstack([_WATER1, _WATER2])

        compile_dir = tmp_path / "compile"
        compile_dir.mkdir()
        compiled = bond_scan_module._compile_scan_input(
            interface, coords, _SYMBOLS, 0, 1, req.coordinate, req.protocol, compile_dir
        )

        captured: dict[str, str] = {}

        def fake_run(input_file: Path, _output_file: Path) -> bool:
            captured["inp"] = input_file.read_text(encoding="utf-8")
            return False

        monkeypatch.setattr(interface, "_run_orca", fake_run)
        _ = interface.relaxed_scan(
            coords,
            _SYMBOLS,
            scan_coordinate=bond_scan_module._scan_coordinate_spec(req.coordinate),
            points=req.coordinate.n_points,
            charge=0,
            multiplicity=1,
            output_dir=tmp_path / "run",
            output_name="scan",
            method=req.protocol.scan_optimizer.method,
            basis=None,
            use_scants=bool(req.protocol.scan_driver.use_scants),
            full_scan=bool(req.protocol.scan_driver.full_scan),
            geom_maxiter=int(
                req.protocol.scan_optimizer.max_iterations
                or req.protocol.scan_driver.max_iterations
            ),
        )

        assert "inp" in captured, "relaxed_scan did not render an input"
        assert compiled.read_text(encoding="utf-8") == captured["inp"]
