"""Behavior tests for the standalone relaxed-scan calculation primitive."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from acp.calculations.contracts import CalculationRequest, JsonValue, StructureArtifact
from acp.calculations.primitives.scan import ScanCoordinateError, run_scan
from acp.storage.manifest import ProductKind, ResultManifest
from tests.conftest import FakeBackend


def _request(tmp_path: Path, coordinate: str = "0,1,1.0,1.5") -> CalculationRequest:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("2\ninput\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\n", encoding="utf-8")
    resources: dict[str, JsonValue] = {
        "backend": "orca",
        "output_dir": str(tmp_path / "WORK" / "07_PATH" / "ORCA"),
        "scan_coordinates": [coordinate],
        "scan_points": 3,
    }
    return CalculationRequest(
        input_artifact=StructureArtifact(
            path=input_path,
            elements=["H", "H"],
            source="test",
        ),
        method="r2SCAN-3c",
        resources=resources,
        workflow="scan",
        profile="default",
    )


def test_run_scan_writes_frames_and_trajectory_product(
    fake_backend: FakeBackend, tmp_path: Path
) -> None:
    # Given: a valid two-atom coordinate and the in-process fake backend.
    request = _request(tmp_path)

    # When: the relaxed-scan primitive runs.
    result = run_scan(request)

    # Then: the backend receives a compiled coordinate plan.
    assert result.status == "completed"
    assert fake_backend.calls[0].method == "relaxed_scan"
    plan = fake_backend.calls[0].kwargs["plan"]
    assert plan.points == 3
    assert plan.coordinates[0].atoms == (0, 1)

    # And: each frame is persisted under RESULT/structures.
    structures_dir = tmp_path / "RESULT" / "structures"
    frame_paths = sorted(structures_dir.glob("scan_frame_*.xyz"))
    assert len(frame_paths) == 3
    assert all(path.is_file() for path in frame_paths)

    # And: the manifest registers a trajectory product and its frame products.
    manifest = ResultManifest.read(tmp_path / "RESULT")
    trajectory_products = [
        product for product in manifest.products if product.kind == ProductKind.TRAJECTORY
    ]
    assert len(trajectory_products) == 1
    trajectory_path = tmp_path / "RESULT" / trajectory_products[0].path
    assert trajectory_path.is_file()
    payload = json.loads(trajectory_path.read_text(encoding="utf-8"))
    assert payload["frame_count"] == 3
    assert result.metadata["frame_count"] == 3


def test_run_scan_rejects_atom_index_before_backend(
    fake_backend: FakeBackend, tmp_path: Path
) -> None:
    # Given: a coordinate that references atom 9 in a two-atom input.
    request = _request(tmp_path, coordinate="1,9,0.9,1.5")

    # When / Then: validation reports the atom constraint without invoking QC.
    with pytest.raises(ScanCoordinateError, match=r"atom index 9"):
        run_scan(request)
    assert fake_backend.calls == []


def test_scan_cli_rejects_atom_index_with_usage_error(tmp_path: Path) -> None:
    input_path = tmp_path / "input.xyz"
    input_path.write_text("2\ninput\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\n", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            str(input_path),
            "--coordinate",
            "0,9,1.0,1.5",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "atom index 9" in f"{completed.stdout}\n{completed.stderr}"


def test_scan_cli_materializes_smiles_and_hands_task_the_xyz(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from acp.cli import _handle_scan, build_parser
    from acp.storage.layout import TaskStorage

    captured: dict[str, object] = {}

    def _fake_run_scan(req, *, progress_reporter=None):
        captured["req"] = req
        return SimpleNamespace(status="completed", metadata={"frame_count": 3}, errors=[])

    monkeypatch.setattr("acp.workflows.simple.run_scan", _fake_run_scan)

    args = build_parser().parse_args(
        [
            "run",
            "scan",
            "--input",
            "CCO",
            "--coordinate",
            "0,1,1.0,1.4",
            "--scan-points",
            "3",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ]
    )
    assert _handle_scan(args) == 0

    req = captured["req"]
    input_path = req.input_artifact.path
    assert input_path.name == "input.xyz"
    assert input_path.is_file()
    assert input_path.parent.parent.name == "out"
    assert req.input_artifact.source == "smiles"
    assert req.input_artifact.elements == ["C", "C", "O", "H", "H", "H", "H", "H", "H"]
    assert req.resources["charge"] == 0
    assert req.resources["multiplicity"] == 1
    assert int(input_path.read_text(encoding="utf-8").splitlines()[0]) == 9

    provenance = json.loads((input_path.parent / "input_source.json").read_text(encoding="utf-8"))
    assert provenance["smiles"] == "CCO"
    assert provenance["embedding"]["seed"] == 42
    assert provenance["atom_count"] == 9
    assert provenance["charge"] == 0
    assert provenance["multiplicity"] == 1
    # materialized file lives in the v2 task root beside WORK/RESULT
    assert TaskStorage(input_path.parent).work_dir().is_dir()


def test_scan_cli_smiles_rejects_out_of_range_atom_before_qc(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            "CCO",
            "--coordinate",
            "0,9,1.0,1.5",
            "--scan-points",
            "2",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 2
    assert "atom index 9" in f"{completed.stdout}\n{completed.stderr}"
    materialized = sorted((tmp_path / "out").glob("*/input.xyz"))
    assert len(materialized) == 1
    assert materialized[0].read_text(encoding="utf-8").splitlines()[0] == "9"


def test_scan_cli_nonexistent_file_reports_failure(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "scan",
            "--input",
            str(tmp_path / "missing.xyz"),
            "--coordinate",
            "0,1,1.0,1.5",
            "--scan-points",
            "2",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "Input file not found" in f"{completed.stdout}\n{completed.stderr}"


def test_materialized_smiles_geometry_binds_scan_identity(tmp_path: Path) -> None:
    from acp.calculations.contracts import CalculationPlan, CalculationStep, StepKind
    from acp.calculations.identity import compute_identity
    from acp.storage.layout import TaskStorage
    from acp.workflows.simple import prepare_scan_input

    input_path = prepare_scan_input("CCO").materialize(TaskStorage(tmp_path))

    def _fingerprint() -> str:
        plan = CalculationPlan(
            workflow="scan",
            profile="default",
            items=[StructureArtifact(path=input_path)],
            steps=[
                CalculationStep(
                    kind=StepKind.SCAN,
                    spec={"scan_coordinates": ["0,1,1.0,1.4"]},
                )
            ],
        )
        return compute_identity(plan).plan_identity

    first = _fingerprint()
    original = input_path.read_text(encoding="utf-8")
    perturbed = original.replace("-0.8883105789", "-0.9883105789", 1)
    assert perturbed != original
    input_path.write_text(perturbed, encoding="utf-8")

    assert _fingerprint() != first


def test_scan_cli_projects_scants_flag_into_resources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from acp.cli import _handle_scan, build_parser

    captured: list[object] = []

    def _fake_run_scan(req, *, progress_reporter=None):
        captured.append(req)
        return SimpleNamespace(status="completed", metadata={}, errors=[])

    monkeypatch.setattr("acp.workflows.simple.run_scan", _fake_run_scan)

    for extra, expected in (([], False), (["--scants"], True)):
        args = build_parser().parse_args(
            [
                "run",
                "scan",
                "--input",
                "CCO",
                "--coordinate",
                "0,1,1.0,1.4",
                "--scan-points",
                "2",
                "--output",
                str(tmp_path / f"out_{expected}"),
                "--log-level",
                "ERROR",
                *extra,
            ]
        )
        assert _handle_scan(args) == 0
        assert captured[-1].resources["use_scants"] is expected


def test_scan_cli_rejects_scants_with_multi_coordinate(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    from acp.cli import _handle_scan, build_parser

    input_path = tmp_path / "m.xyz"
    input_path.write_text("3\nm\nH 0.0 0.0 0.0\nH 0.0 0.0 1.0\nH 0.0 1.0 0.0\n", encoding="utf-8")
    args = build_parser().parse_args(
        [
            "run",
            "scan",
            "--input",
            str(input_path),
            "--coordinate",
            "0,1,1.0,1.5",
            "--coordinate",
            "1,2,1.0,1.5",
            "--scants",
            "--output",
            str(tmp_path / "out"),
            "--log-level",
            "ERROR",
        ]
    )
    with caplog.at_level(logging.ERROR):
        assert _handle_scan(args) == 2
    assert "unavailable for synchronous" in caplog.text


def _scan_job_spec(*, use_scants: bool) -> object:
    from acp.scheduler.jobs import JobSpec

    return JobSpec(
        workflow="scan",
        input={"source": "input.xyz", "source_type": "file", "coordinate": "0,1,1.0,1.4"},
        method={"levels": {"scan": {"scan_use_scants": use_scants, "scan_coordinate_points": 3}}},
        resources={"nproc": 4},
    )


def test_scan_method_flags_emits_scants_iff_enabled() -> None:
    from acp.scheduler.jobs import scan_method_flags

    enabled = scan_method_flags({"scan_coordinates": "0,1,1.0,1.4", "scan_use_scants": True}, {})
    disabled = scan_method_flags({"scan_coordinates": "0,1,1.0,1.4", "scan_use_scants": False}, {})
    absent = scan_method_flags({"scan_coordinates": "0,1,1.0,1.4"}, {})
    assert enabled.count("--scants") == 1
    assert "--scants" not in disabled
    assert "--scants" not in absent


def test_scan_method_flags_emits_multi_atom_coordinates() -> None:
    """Mapping coordinates with 2-4 atoms render comma-joined; string entries
    stay blind pass-through (the generator never validates)."""
    from acp.scheduler.jobs import scan_method_flags

    angle = scan_method_flags(
        {"scan_points": 2}, {"coordinate": {"atoms": [0, 1, 2], "start": 90, "end": 120}}
    )
    assert angle == ["--coordinate", "0,1,2,90,120", "--scan-points", "2"]
    dihedral = scan_method_flags(
        {"scan_points": 2}, {"coordinate": {"atoms": [0, 1, 2, 3], "start": 90, "end": 120}}
    )
    assert dihedral == ["--coordinate", "0,1,2,3,90,120", "--scan-points", "2"]
    five_part = scan_method_flags({"scan_points": 2}, {"coordinate": "0,1,2,90,120"})
    assert five_part == ["--coordinate", "0,1,2,90,120", "--scan-points", "2"]
    six_part = scan_method_flags({"scan_points": 2}, {"coordinate": "0,1,2,3,90,120"})
    assert six_part == ["--coordinate", "0,1,2,3,90,120", "--scan-points", "2"]
    with pytest.raises(ValueError, match="2-4 atoms"):
        scan_method_flags(
            {"scan_points": 2}, {"coordinate": {"atoms": [0], "start": 1.0, "end": 2.0}}
        )


def test_scan_method_flags_rejects_mapping_kind_atom_count_mismatch() -> None:
    """F1: an explicit ``kind`` in a mapping coordinate pins the atom count.

    The legacy queue path bypasses ``validate_scan_submission``, so a mapping
    like ``{"kind": "angle", "atoms": [0, 1]}`` must be rejected by the
    generator itself instead of silently running a DISTANCE scan.
    """
    from acp.scheduler.jobs import scan_method_flags

    with pytest.raises(ValueError, match="requires 3 atoms"):
        scan_method_flags(
            {"scan_points": 2},
            {"coordinate": {"kind": "angle", "atoms": [0, 1], "start": 90, "end": 120}},
        )
    with pytest.raises(ValueError, match="requires 4 atoms"):
        scan_method_flags(
            {"scan_points": 2},
            {"coordinate": {"kind": "dihedral", "atoms": [0, 1, 2], "start": 0, "end": 90}},
        )
    with pytest.raises(ValueError, match="unsupported"):
        scan_method_flags(
            {"scan_points": 2},
            {"coordinate": {"kind": "torsion", "atoms": [0, 1, 2, 3], "start": 0, "end": 90}},
        )
    # Explicit kind with the matching count still emits.
    distance = scan_method_flags(
        {"scan_points": 2},
        {"coordinate": {"kind": "distance", "atoms": [0, 1], "start": 1.0, "end": 2.0}},
    )
    assert distance == ["--coordinate", "0,1,1.0,2.0", "--scan-points", "2"]
    # Back-compat: mappings WITHOUT kind keep atom-count inference (2-4 atoms).
    inferred = scan_method_flags(
        {"scan_points": 2}, {"coordinate": {"atoms": [0, 1, 2], "start": 90, "end": 120}}
    )
    assert inferred == ["--coordinate", "0,1,2,90,120", "--scan-points", "2"]
    # No-kind mappings outside the 2-4 window keep the pre-existing error.
    with pytest.raises(ValueError, match="2-4 atoms"):
        scan_method_flags(
            {"scan_points": 2},
            {"coordinate": {"atoms": [0, 1, 2, 3, 4], "start": 90, "end": 120}},
        )


def test_scan_method_flags_normalizes_integral_float_points() -> None:
    """F2: an integral float ``scan_points`` passes the validation boundary but
    must emit the integer form — argparse rejects ``--scan-points 21.0``."""
    from acp.scheduler.jobs import scan_method_flags

    float_integral = scan_method_flags({"scan_points": 21.0}, {"coordinate": "0,1,1.0,1.4"})
    assert float_integral == ["--coordinate", "0,1,1.0,1.4", "--scan-points", "21"]
    int_points = scan_method_flags({"scan_points": 21}, {"coordinate": "0,1,1.0,1.4"})
    assert int_points == ["--coordinate", "0,1,1.0,1.4", "--scan-points", "21"]
    # Non-integral floats pass through unchanged (validated paths reject them
    # at the boundary; the generator stays a generator).
    non_integral = scan_method_flags({"scan_points": 21.5}, {"coordinate": "0,1,1.0,1.4"})
    assert non_integral == ["--coordinate", "0,1,1.0,1.4", "--scan-points", "21.5"]
    # The levels-scoped resolution gets the same normalization.
    from_levels = scan_method_flags(
        {"levels": {"scan": {"scan_coordinate_points": 7.0}}},
        {"coordinate": "0,1,1.0,1.4"},
    )
    assert from_levels == ["--coordinate", "0,1,1.0,1.4", "--scan-points", "7"]


def test_scan_scants_projected_by_local_and_remote_argv(tmp_path: Path) -> None:
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.runner import JobRunner

    runner = JobRunner(python_executable="python")
    for enabled in (False, True):
        spec = _scan_job_spec(use_scants=enabled)
        local = runner._build_cmd(spec, tmp_path, input_path="input.xyz")
        remote = build_remote_cli_command(spec, input_path="input.xyz")
        assert ("--scants" in local) is enabled
        assert ("--scants" in remote) is enabled
        assert local.index("--scants") == remote.index("--scants") if enabled else True


# ── Submission-boundary validation (todo 9 / GAP-8) ──────────────────────
#
# ``validate_scan_submission`` is the shared ACP submission-boundary
# validator used by both v1 create/edit and v2 batch submission.  The
# parameter generator (``scan_method_flags``) is deliberately NOT a
# validator; these tests pin the boundary rules themselves.

XYZ_TRIATOMIC = "3\ntriatomic\nO 0.0 0.0 0.0\nC 1.2 0.0 0.0\nH 2.0 0.0 0.0\n"
XYZ_TETRATOMIC = (
    "4\ntetratomic\nC 0.0 0.0 0.0\nH 1.09 0.0 0.0\nH -0.36 1.03 0.0\nH -0.36 -0.51 0.89\n"
)


def _scan_method(points: object = 21) -> dict:
    return {"schema_id": "dft_scan", "scan_points": points}


def _scan_input(coordinate: object = "0,1,1.0,2.0", xyz: str | None = XYZ_TRIATOMIC) -> dict:
    inp: dict = {"source_type": "xyz_text", "charge": 0, "multiplicity": 1}
    if xyz is not None:
        inp["source"] = xyz
    if coordinate is not None:
        inp["scan_coordinates"] = [coordinate]
    return inp


def test_scan_submission_requires_coordinates() -> None:
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="coordinate"):
        validate_scan_submission("scan", _scan_method(), _scan_input(coordinate=None))


def test_scan_submission_rejects_empty_coordinates_list() -> None:
    """GAP-8: an empty list used to pass the generator and yield only
    ``--scan-points``; the boundary must reject it."""
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="coordinate"):
        validate_scan_submission("scan", _scan_method(), _scan_input(coordinate=[]))


@pytest.mark.parametrize(
    ("coordinate", "match"),
    [
        ("0,0,1.0,2.0", "different atoms"),
        ("-1,1,1.0,2.0", "0-based"),
        ("0,-2,1.0,2.0", "0-based"),
        ("1.5,1,1.0,2.0", "integer"),
        ("a,1,1.0,2.0", "integer"),
        ("0,3,1.0,2.0", "out of range"),
        ("3,4,1.0,2.0", "out of range"),
        ("0,1,nan,2.0", "finite"),
        ("0,1,1.0,inf", "finite"),
        ("0,1,-1.0,2.0", "greater than 0"),
        ("0,1,0.0,2.0", "greater than 0"),
        ("0,1,2.0,2.0", "differ"),
        ("0,1,1.0,1.0000000001", "differ"),
        ("0,1,1.0", "atom1,atom2"),
    ],
)
def test_scan_submission_rejects_invalid_coordinates(coordinate: str, match: str) -> None:
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match=match):
        validate_scan_submission("scan", _scan_method(), _scan_input(coordinate=coordinate))


@pytest.mark.parametrize("points", [1, 0, -3, 2.5, "x", True])
def test_scan_submission_rejects_invalid_points(points: object) -> None:
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="scan_points"):
        validate_scan_submission("scan", _scan_method(points=points), _scan_input())


def test_scan_submission_accepts_valid_distance_coordinate() -> None:
    from acp.scheduler.jobs import validate_scan_submission

    validate_scan_submission("scan", _scan_method(points=2), _scan_input(coordinate="0,1,1.0,2.0"))
    validate_scan_submission(
        "scan",
        _scan_method(points=None),
        _scan_input(coordinate={"atoms": [0, 2], "start": 1.0, "end": 2.5}),
    )
    validate_scan_submission(
        "scan",
        {"levels": {"scan": {"scan_coordinate_points": 5}}},
        {
            "source_type": "xyz_text",
            "source": XYZ_TRIATOMIC,
            "scan_coordinates": ["0,1,1.0,2.0", "1,2,1.5,2.5"],
        },
    )


def test_scan_submission_accepts_angle_and_dihedral_coordinates() -> None:
    """Kind-aware boundary: 3-atom angle and 4-atom dihedral scans pass in
    both the string form (kind inferred from atom count) and the mapping
    form (explicit ``kind``)."""
    from acp.scheduler.jobs import validate_scan_submission

    validate_scan_submission("scan", _scan_method(), _scan_input(coordinate="0,1,2,90,120"))
    validate_scan_submission(
        "scan",
        _scan_method(),
        _scan_input(coordinate="0,1,2,3,-180.0,180.0", xyz=XYZ_TETRATOMIC),
    )
    validate_scan_submission(
        "scan",
        _scan_method(),
        _scan_input(coordinate={"kind": "angle", "atoms": [0, 1, 2], "start": 100, "end": 160}),
    )
    validate_scan_submission(
        "scan",
        _scan_method(),
        _scan_input(
            coordinate={"kind": "dihedral", "atoms": [0, 1, 2, 3], "start": -60, "end": 60},
            xyz=XYZ_TETRATOMIC,
        ),
    )


def test_scan_submission_rejects_kind_atom_count_mismatch() -> None:
    """Object form: unknown ``kind`` and kind/atom-count mismatches rejected."""
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="unsupported scan coordinate kind"):
        validate_scan_submission(
            "scan",
            _scan_method(),
            _scan_input(
                coordinate={"kind": "torsion", "atoms": [0, 1, 2, 3], "start": 0, "end": 90}
            ),
        )
    with pytest.raises(ValueError, match="angle coordinates require 3 atoms"):
        validate_scan_submission(
            "scan",
            _scan_method(),
            _scan_input(coordinate={"kind": "angle", "atoms": [0, 1], "start": 90, "end": 120}),
        )
    with pytest.raises(ValueError, match="dihedral coordinates require 4 atoms"):
        validate_scan_submission(
            "scan",
            _scan_method(),
            _scan_input(
                coordinate={"kind": "dihedral", "atoms": [0, 1, 2], "start": 0, "end": 90},
                xyz=XYZ_TETRATOMIC,
            ),
        )


@pytest.mark.parametrize(
    ("coordinate", "match"),
    [
        ("0,1,2,90,181", "0 and 180"),
        ("0,1,2,-5,120", "0 and 180"),
        ("0,1,2,3,-180.0,361.0", "-360 and 360"),
        ("0,1,2,3,-400.0,180.0", "-360 and 360"),
    ],
)
def test_scan_submission_rejects_out_of_range_angle_and_dihedral(
    coordinate: str, match: str
) -> None:
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match=match):
        validate_scan_submission(
            "scan", _scan_method(), _scan_input(coordinate=coordinate, xyz=XYZ_TETRATOMIC)
        )


def test_scan_submission_rejects_duplicate_atoms_in_angle_coordinate() -> None:
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="different atoms"):
        validate_scan_submission("scan", _scan_method(), _scan_input(coordinate="0,1,1,90,120"))


def test_scan_submission_rejects_unverifiable_atom_selection() -> None:
    """No confirmed structure -> clear validation result, never an
    unverifiable atom selection."""
    from acp.scheduler.jobs import validate_scan_submission

    with pytest.raises(ValueError, match="confirmed structure"):
        validate_scan_submission(
            "scan",
            _scan_method(),
            {"source_type": "smiles", "source": "CCO", "scan_coordinates": ["0,1,1.0,2.0"]},
        )


def test_scan_submission_uses_explicit_atom_count_when_input_has_no_geometry() -> None:
    from acp.scheduler.jobs import validate_scan_submission

    validate_scan_submission(
        "scan",
        _scan_method(),
        {"source_type": "structure_asset", "source": "a.xyz", "scan_coordinates": ["2,3,1.0,2.0"]},
        atom_count=4,
    )
    with pytest.raises(ValueError, match="out of range"):
        validate_scan_submission(
            "scan",
            _scan_method(),
            {
                "source_type": "structure_asset",
                "source": "a.xyz",
                "scan_coordinates": ["2,3,1.0,2.0"],
            },
            atom_count=3,
        )


def test_scan_submission_ignores_other_workflows() -> None:
    from acp.scheduler.jobs import validate_scan_submission

    validate_scan_submission("optimize", _scan_method(), {"source_type": "smiles", "source": "C"})


# ── Real submission path: wizard-shaped body -> v1 -> runner argv ────────


@pytest.fixture()
def scan_client(tmp_path: Path):
    import os

    from fastapi.testclient import TestClient

    os.environ["ACP_RUN_ROOT"] = str(tmp_path)
    from acp.api.server import create_app

    app = create_app(run_root=tmp_path, max_running=1)
    with TestClient(app) as c:
        yield c


def _fake_scheduler(monkeypatch: pytest.MonkeyPatch, captured: list) -> None:
    """Capture the submitted JobSpec instead of queueing real work."""
    from acp.scheduler.jobs import JobRecord, JobSpec, JobStatus
    from acp.scheduler.manager import JobManager

    def _submit(self: JobManager, spec: JobSpec) -> JobRecord:
        captured.append(spec)
        return JobRecord(
            id=f"scanjob_{len(captured):03d}",
            spec=spec,
            status=JobStatus.QUEUED,
            work_dir=str(
                Path(self.run_root) / "WORK" / "00_RUNTIME" / f"scanjob_{len(captured):03d}"
            ),
        )

    monkeypatch.setattr(JobManager, "submit", _submit)


def _wizard_body(
    *,
    coordinate: object = "0,1,1.0,3.0",
    points: object = 21,
    xyz: str = XYZ_TRIATOMIC,
) -> dict:
    """The body shape ``submitJobModal`` builds for a scan wizard submit."""
    method: dict = {
        "schema_id": "dft_scan",
        "profile_id": "default",
        "scan_points": points,
        "levels": {
            "scan": {
                "engine": "orca",
                "functional": "r2SCAN-3c",
                "scan_coordinate_kind": "distance",
                "scan_coordinate_start": 1.0,
                "scan_coordinate_end": 3.0,
                "scan_coordinate_points": points,
            }
        },
    }
    inp: dict = {
        "source_type": "xyz_text",
        "source": xyz,
        "charge": 0,
        "multiplicity": 1,
    }
    if coordinate is not None:
        inp["scan_coordinates"] = [coordinate] if not isinstance(coordinate, list) else coordinate
    return {
        "workflow": "scan",
        "molecule_name": "wizard_scan",
        "input": inp,
        "method": method,
        "resources": {"nproc": 4, "mem": "8GB"},
        "execution_mode": "local",
    }


def test_wizard_body_passes_v1_and_reaches_runner_flags(
    scan_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.scheduler.runner import JobRunner

    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    response = scan_client.post("/api/v1/jobs", json=_wizard_body())
    assert response.status_code == 201, response.text
    assert len(captured) == 1
    spec = captured[0]
    cmd = JobRunner(python_executable="python")._build_cmd(spec, tmp_path, input_path="input.xyz")
    coord_at = cmd.index("--coordinate")
    assert cmd[coord_at + 1] == "0,1,1.0,3.0"
    points_at = cmd.index("--scan-points")
    assert cmd[points_at + 1] == "21"


def test_angle_scan_body_through_v1_reaches_local_and_remote_argv(
    scan_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coverage: a 5-part ANGLE coordinate flows real v1 create → JobSpec →
    local runner argv and remote argv (E7 parity)."""
    from acp.scheduler.remote.script_gen import build_remote_cli_command
    from acp.scheduler.runner import JobRunner

    body = _wizard_body(coordinate="0,1,2,90,120", xyz=XYZ_TRIATOMIC)
    body["method"]["levels"]["scan"].update(
        {"scan_coordinate_kind": "angle", "scan_coordinate_start": 90, "scan_coordinate_end": 120}
    )
    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    response = scan_client.post("/api/v1/jobs", json=body)
    assert response.status_code == 201, response.text
    assert len(captured) == 1
    spec = captured[0]
    cmd = JobRunner(python_executable="python")._build_cmd(spec, tmp_path, input_path="input.xyz")
    assert cmd[cmd.index("--coordinate") + 1] == "0,1,2,90,120"
    assert cmd[cmd.index("--scan-points") + 1] == "21"
    remote = build_remote_cli_command(spec, input_path="input.xyz")
    assert remote[remote.index("--coordinate") + 1] == "0,1,2,90,120"
    assert remote[remote.index("--scan-points") + 1] == "21"


def test_dihedral_scan_body_through_v1_reaches_runner_argv(
    scan_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Coverage: a 6-part DIHEDRAL coordinate (4-atom xyz) flows v1 → argv."""
    from acp.scheduler.runner import JobRunner

    body = _wizard_body(coordinate="0,1,2,3,-180.0,180.0", xyz=XYZ_TETRATOMIC, points=15.0)
    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    response = scan_client.post("/api/v1/jobs", json=body)
    assert response.status_code == 201, response.text
    assert len(captured) == 1
    cmd = JobRunner(python_executable="python")._build_cmd(
        captured[0], tmp_path, input_path="input.xyz"
    )
    assert cmd[cmd.index("--coordinate") + 1] == "0,1,2,3,-180.0,180.0"
    assert cmd[cmd.index("--scan-points") + 1] == "15"


@pytest.mark.parametrize(
    ("coordinate", "points", "match"),
    [
        (None, 21, "coordinate"),
        ([], 21, "coordinate"),
        ("1,1,1.0,3.0", 21, "different atoms"),
        ("-1,2,1.0,3.0", 21, "0-based"),
        ("0,1.5,1.0,3.0", 21, "integer"),
        ("0,3,1.0,3.0", 21, "out of range"),
        ("0,1,nan,3.0", 21, "finite"),
        ("0,1,0.0,3.0", 21, "greater than 0"),
        ("0,1,2.0,2.0", 21, "differ"),
        ("0,1,1.0,3.0", 1, "scan_points"),
    ],
)
def test_wizard_body_invalid_matrix_rejected_by_v1(
    scan_client,
    monkeypatch: pytest.MonkeyPatch,
    coordinate: object,
    points: object,
    match: str,
) -> None:
    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    response = scan_client.post(
        "/api/v1/jobs", json=_wizard_body(coordinate=coordinate, points=points)
    )
    assert response.status_code == 422, response.text
    assert match in response.text
    assert captured == []


def test_v2_batch_scan_item_failure_is_per_item(
    scan_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    good = _wizard_body()
    bad = _wizard_body(coordinate="0,0,1.0,3.0")
    response = scan_client.post(
        "/api/v2/tasks/batch",
        json={
            "tasks": [
                {
                    "molecule_name": "good_scan",
                    "task_name": "scan",
                    "workflow": "scan",
                    "input": good["input"],
                    "method": good["method"],
                    "resources": good["resources"],
                    "execution_mode": "local",
                },
                {
                    "molecule_name": "bad_scan",
                    "task_name": "scan",
                    "workflow": "scan",
                    "input": bad["input"],
                    "method": bad["method"],
                    "resources": bad["resources"],
                    "execution_mode": "local",
                },
            ]
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created"]) == 1
    assert body["created"][0]["molecule_name"] == "good_scan"
    assert len(body["failed"]) == 1
    assert body["failed"][0]["molecule_name"] == "bad_scan"
    assert "different atoms" in body["failed"][0]["error"]
    assert len(captured) == 1


# ── Executed wizard builder: real body -> real v1 -> runner argv ─────────

_WIZARD_HTML = Path(__file__).resolve().parents[1] / "frontend" / "ACP_Workbench_v2.html"
# Local 4-atom geometry for the dihedral builder cases (self-contained so the
# node-harness section never depends on the backend-boundary fixtures).
_WIZARD_XYZ_TETRA = (
    "4\ntetratomic\nC 0.0 0.0 0.0\nH 1.09 0.0 0.0\nH -0.36 1.03 0.0\nH -0.36 -0.51 0.89\n"
)


def _run_wizard_builder_in_node(cases: list[dict]) -> list[dict]:
    """Execute the page's pure scan builder (no DOM) for each case via node."""
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("node"):
        pytest.skip("node not available")
    html = _WIZARD_HTML.read_text(encoding="utf-8")
    start = html.index("function scanWizardCountAtoms")
    end = html.index("function scanWizardErrorMessage")
    source = html[start:end]
    script = (
        source
        + "\nconst cases = "
        + json.dumps(cases)
        + """
const out = [];
for (const c of cases) {
  out.push({ name: c.name, result: buildScanWizardBodyParts(c.fields, c.structure) });
}
console.log(JSON.stringify(out));
"""
    )
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    try:
        result = subprocess.run(["node", path], capture_output=True, text=True, timeout=60)
    finally:
        Path(path).unlink(missing_ok=True)
    assert result.returncode == 0, f"node builder run failed:\n{result.stderr}"
    return json.loads(result.stdout)


def test_wizard_builder_converts_one_based_exactly_once() -> None:
    results = {
        item["name"]: item["result"]
        for item in _run_wizard_builder_in_node(
            [
                {
                    "name": "valid",
                    "fields": {
                        "atomA": "1",
                        "atomB": "2",
                        "start": "1.0",
                        "end": "3.0",
                        "points": "21",
                    },
                    "structure": {"xyz": XYZ_TRIATOMIC},
                },
                {
                    "name": "two_atoms",
                    "fields": {
                        "atomA": "2",
                        "atomB": "3",
                        "start": "1.5",
                        "end": "2.5",
                        "points": "3",
                    },
                    "structure": {"xyz": XYZ_TRIATOMIC},
                },
                {
                    "name": "angle",
                    "fields": {
                        "kind": "angle",
                        "atomA": "1",
                        "atomB": "2",
                        "atomC": "3",
                        "start": "100.0",
                        "end": "160.0",
                        "points": "21",
                    },
                    "structure": {"xyz": XYZ_TRIATOMIC},
                },
                {
                    "name": "dihedral",
                    "fields": {
                        "kind": "dihedral",
                        "atomA": "1",
                        "atomB": "2",
                        "atomC": "3",
                        "atomD": "4",
                        "start": "0.0",
                        "end": "180.0",
                        "points": "21",
                    },
                    "structure": {"xyz": _WIZARD_XYZ_TETRA},
                },
            ]
        )
    }
    valid = results["valid"]
    assert valid["ok"] is True
    assert valid["input"]["scan_coordinates"] == ["0,1,1.0,3.0"]
    assert valid["method"]["scan_points"] == 21
    assert valid["mirror"]["scan_coordinate_atoms"] == [1, 2]
    assert valid["mirror"]["scan_coordinate_kind"] == "distance"
    two = results["two_atoms"]
    assert two["input"]["scan_coordinates"] == ["1,2,1.5,2.5"]
    assert two["atomCount"] == 3
    angle = results["angle"]
    assert angle["ok"] is True
    assert angle["input"]["scan_coordinates"] == ["0,1,2,100.0,160.0"]
    assert angle["mirror"]["scan_coordinate_kind"] == "angle"
    assert angle["mirror"]["scan_coordinate_atoms"] == [1, 2, 3]
    dihedral = results["dihedral"]
    assert dihedral["ok"] is True
    assert dihedral["input"]["scan_coordinates"] == ["0,1,2,3,0.0,180.0"]
    assert dihedral["mirror"]["scan_coordinate_kind"] == "dihedral"
    assert dihedral["mirror"]["scan_coordinate_atoms"] == [1, 2, 3, 4]


def test_wizard_builder_rejection_matrix() -> None:
    base = {"atomA": "1", "atomB": "2", "start": "1.0", "end": "3.0", "points": "21"}
    cases = [
        ("empty_fields", {**base, "atomA": ""}, XYZ_TRIATOMIC, "fields_required"),
        ("duplicate", {**base, "atomB": "1"}, XYZ_TRIATOMIC, "atoms_not_distinct"),
        ("non_integer", {**base, "atomA": "1.5"}, XYZ_TRIATOMIC, "atom_not_integer"),
        ("negative", {**base, "atomA": "-1"}, XYZ_TRIATOMIC, "atom_not_positive"),
        ("out_of_range", {**base, "atomB": "4"}, XYZ_TRIATOMIC, "atom_out_of_range"),
        ("range_nan", {**base, "start": "nan"}, XYZ_TRIATOMIC, "range_not_finite"),
        ("range_zero", {**base, "start": "0"}, XYZ_TRIATOMIC, "range_not_positive"),
        ("range_equal", {**base, "end": "1.0"}, XYZ_TRIATOMIC, "range_equal"),
        ("points_one", {**base, "points": "1"}, XYZ_TRIATOMIC, "points_invalid"),
        ("points_fraction", {**base, "points": "2.5"}, XYZ_TRIATOMIC, "points_invalid"),
        ("no_structure", base, {"smiles": "CCO"}, "no_confirmed_structure"),
        ("angle_missing_atom_3", {**base, "kind": "angle"}, XYZ_TRIATOMIC, "fields_required"),
        (
            "angle_out_of_bounds",
            {**base, "kind": "angle", "atomC": "3", "start": "181", "end": "160"},
            XYZ_TRIATOMIC,
            "range_angle_invalid",
        ),
        (
            "dihedral_out_of_bounds",
            {**base, "kind": "dihedral", "atomC": "3", "atomD": "4", "start": "-400", "end": "180"},
            _WIZARD_XYZ_TETRA,
            "range_dihedral_invalid",
        ),
        (
            "angle_duplicate_atoms",
            {**base, "kind": "angle", "atomC": "2"},
            XYZ_TRIATOMIC,
            "atoms_not_distinct",
        ),
        (
            "angle_atom_3_out_of_range",
            {**base, "kind": "angle", "atomC": "4"},
            XYZ_TRIATOMIC,
            "atom_out_of_range",
        ),
        ("kind_unknown", {**base, "kind": "quadrilateral"}, XYZ_TRIATOMIC, "kind_invalid"),
    ]
    results = {
        item["name"]: item["result"]
        for item in _run_wizard_builder_in_node(
            [
                {"name": name, "fields": fields, "structure": {"xyz": xyz}}
                if not isinstance(xyz, dict)
                else {"name": name, "fields": fields, "structure": xyz}
                for name, fields, xyz, _expected in cases
            ]
        )
    }
    for name, _fields, _xyz, expected in cases:
        result = results[name]
        assert result["ok"] is False, name
        assert result["code"] == expected, name


def test_executed_wizard_body_through_real_v1_reaches_runner_argv(
    scan_client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from acp.scheduler.runner import JobRunner

    built = _run_wizard_builder_in_node(
        [
            {
                "name": "valid",
                "fields": {
                    "atomA": "1",
                    "atomB": "2",
                    "start": "1.0",
                    "end": "3.0",
                    "points": "21",
                },
                "structure": {"xyz": XYZ_TRIATOMIC},
            }
        ]
    )[0]["result"]
    assert built["ok"] is True
    body = {
        "workflow": "scan",
        "molecule_name": "wizard_scan",
        "input": {
            "source_type": "xyz_text",
            "source": XYZ_TRIATOMIC,
            "charge": 0,
            "multiplicity": 1,
            "scan_coordinates": built["input"]["scan_coordinates"],
        },
        "method": {
            "schema_id": "dft_scan",
            "profile_id": "default",
            "scan_points": built["method"]["scan_points"],
            "levels": {"scan": dict(built["mirror"], engine="orca", functional="r2SCAN-3c")},
        },
        "resources": {"nproc": 4, "mem": "8GB"},
        "execution_mode": "local",
    }
    captured: list = []
    _fake_scheduler(monkeypatch, captured)
    response = scan_client.post("/api/v1/jobs", json=body)
    assert response.status_code == 201, response.text
    spec = captured[0]
    assert spec.input["scan_coordinates"] == ["0,1,1.0,3.0"]
    cmd = JobRunner(python_executable="python")._build_cmd(spec, tmp_path, input_path="input.xyz")
    coord_at = cmd.index("--coordinate")
    assert cmd[coord_at + 1] == "0,1,1.0,3.0"
    points_at = cmd.index("--scan-points")
    assert cmd[points_at + 1] == "21"
