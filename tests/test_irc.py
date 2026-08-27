"""Tests for the IRC calculation primitive and endpoint validation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations import CalculationPlan
from acp.calculations.contracts import StructureArtifact, StructureRole
from acp.calculations.plans import IrcRequest, build_irc_request
from acp.calculations.primitives.irc import run_irc
from acp.calculations.primitives.irc import run_irc as primitive_run_irc
from acp.scheduler.jobs import JobSpec
from acp.scheduler.remote.script_gen import build_remote_cli_command
from acp.scheduler.runner import JobRunner
from acp.scheduler.stage_tasks import get_stage_plan
from acp.storage.manifest import ProductKind, ResultManifest
from acp.workflows.irc import run_irc_workflow
from acp.workflows.registry import get_workflow_entry
from acp.workflows.simple import run_irc as simple_run_irc
from cccp.utils import file_io

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts_artifact(
    tmp_path: Path, *, role: StructureRole = StructureRole.TRANSITION_STATE
) -> StructureArtifact:
    """Create a minimal TS artifact with a valid XYZ file."""
    xyz_path = tmp_path / "ts_input.xyz"
    symbols = ["C", "C", "H", "H", "H", "H"]
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.5, 0.0, 0.0],
            [-0.5, 0.9, 0.0],
            [-0.5, -0.9, 0.0],
            [2.0, 0.9, 0.0],
            [2.0, -0.9, 0.0],
        ],
        dtype=float,
    )
    file_io.write_xyz(xyz_path, coords, symbols, title="TS input")
    return StructureArtifact(
        path=xyz_path,
        elements=symbols,
        role=role,
        source="test",
        candidate_id="ts_001",
    )


def _fake_irc_with_endpoints(
    tmp_path: Path,
    backend: Any,
    directions: tuple[str, ...] = ("forward", "reverse"),
    output_dir: Path | None = None,
) -> None:
    """Configure the fake backend to create IRC endpoint files and return them."""
    output_dir = output_dir or tmp_path / "irc_work"
    output_dir.mkdir(parents=True, exist_ok=True)

    symbols = ["C", "C", "H", "H", "H", "H"]
    endpoint_data: dict[str, dict[str, Any]] = {}

    for direction in directions:
        letter = "f" if direction == "forward" else "r"
        endpoint_path = output_dir / f"irc_{letter}.xyz"
        # Slightly different geometry per direction
        offset = 0.1 if direction == "forward" else -0.1
        coords = np.array(
            [
                [0.0 + offset, 0.0, 0.0],
                [1.5 + offset, 0.0, 0.0],
                [-0.5 + offset, 0.9, 0.0],
                [-0.5 + offset, -0.9, 0.0],
                [2.0 + offset, 0.9, 0.0],
                [2.0 + offset, -0.9, 0.0],
            ],
            dtype=float,
        )
        file_io.write_xyz(endpoint_path, coords, symbols, title=f"IRC {direction} endpoint")
        endpoint_data[direction] = {
            "path": endpoint_path,
            "coordinates": coords,
            "symbols": symbols,
        }

    endpoints_dict = {d: info["path"] for d, info in endpoint_data.items()}
    final_geometries = {d: info["coordinates"] for d, info in endpoint_data.items()}

    backend.set_result(
        "irc",
        QCResult(
            success=True,
            energy=-77.0,
            coordinates=np.zeros((6, 3)),
            symbols=symbols,
            converged=True,
            output_file=output_dir / "irc.out",
            log_file=output_dir / "irc.log",
            metadata={
                "endpoints": {d: str(p) for d, p in endpoints_dict.items()},
                "final_geometries": {d: coords.tolist() for d, coords in final_geometries.items()},
            },
        ),
    )


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------


class TestIrcBothDirections:
    """test_irc_both_directions — QA happy path."""

    def test_irc_both_directions(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        """Fake backend produces irc_f.xyz / irc_r.xyz → 2 endpoint structures
        materialised + manifest has 2 IRC_ENDPOINT products."""
        ts = _ts_artifact(tmp_path)
        _fake_irc_with_endpoints(tmp_path, fake_backend, directions=("forward", "reverse"))

        result = run_irc(
            ts,
            directions=("forward", "reverse"),
            resources={
                "output_dir": str(tmp_path / "irc_work"),
                "result_dir": str(tmp_path / "RESULT"),
            },
        )

        # Result status
        assert result.status == "completed", (
            f"Expected completed, got {result.status}: {result.errors}"
        )
        assert not result.errors

        # Backend was called
        assert len(fake_backend.calls) == 1
        call = fake_backend.calls[0]
        assert call.method == "irc"

        # Endpoint files materialised
        result_dir = tmp_path / "RESULT"
        irc_dir = result_dir / "irc"
        forward_path = irc_dir / "irc_forward.xyz"
        reverse_path = irc_dir / "irc_reverse.xyz"
        assert forward_path.exists(), f"Missing forward endpoint: {forward_path}"
        assert reverse_path.exists(), f"Missing reverse endpoint: {reverse_path}"

        # Verify XYZ content
        fwd_coords, fwd_symbols = file_io.read_xyz(forward_path)
        assert len(fwd_symbols) == 6
        assert fwd_coords.shape == (6, 3)

        # Manifest has 2 IRC_ENDPOINT products
        manifest_path = result_dir / "result_manifest.json"
        assert manifest_path.exists(), "Missing result_manifest.json"
        manifest = ResultManifest.read(result_dir)
        irc_products = [p for p in manifest.products if p.kind == ProductKind.IRC_ENDPOINT]
        assert len(irc_products) == 2, f"Expected 2 IRC_ENDPOINT products, got {len(irc_products)}"
        product_ids = {p.id for p in irc_products}
        assert "irc_forward_endpoint" in product_ids
        assert "irc_reverse_endpoint" in product_ids

        # Artifacts
        assert len(result.artifacts) == 2
        artifact_paths = {a.path for a in result.artifacts}
        assert forward_path in artifact_paths
        assert reverse_path in artifact_paths

    def test_irc_forward_only(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        """Single-direction IRC: only forward endpoint materialised."""
        ts = _ts_artifact(tmp_path)
        _fake_irc_with_endpoints(tmp_path, fake_backend, directions=("forward",))

        result = run_irc(
            ts,
            directions=("forward",),
            resources={
                "output_dir": str(tmp_path / "irc_work"),
                "result_dir": str(tmp_path / "RESULT"),
            },
        )

        assert result.status == "completed"
        result_dir = tmp_path / "RESULT"
        irc_dir = result_dir / "irc"
        assert (irc_dir / "irc_forward.xyz").exists()

        manifest = ResultManifest.read(result_dir)
        irc_products = [p for p in manifest.products if p.kind == ProductKind.IRC_ENDPOINT]
        assert len(irc_products) == 1
        assert irc_products[0].id == "irc_forward_endpoint"

    def test_ts_tagged_artifact_accepted(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        """TAG: TS | candidate_id=... comment input → accepted (StructureRole.TRANSITION_STATE)."""
        ts = _ts_artifact(tmp_path, role=StructureRole.TRANSITION_STATE)
        _fake_irc_with_endpoints(tmp_path, fake_backend)

        result = run_irc(
            ts,
            resources={
                "output_dir": str(tmp_path / "irc_work"),
                "result_dir": str(tmp_path / "RESULT"),
            },
        )
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# Failure tests
# ---------------------------------------------------------------------------


class TestIrcRequiresTsRole:
    """test_irc_requires_ts_role — QA failure path."""

    def test_irc_requires_ts_role(self, tmp_path: Path) -> None:
        """Non-TS artifact → request rejected (ValueError)."""
        min_artifact = _ts_artifact(tmp_path, role=StructureRole.MINIMUM)
        with pytest.raises(ValueError, match="transition-state"):
            run_irc(min_artifact)

    def test_irc_default_role_rejected(self, tmp_path: Path) -> None:
        """Default role (MINIMUM) → rejected."""
        xyz_path = tmp_path / "min.xyz"
        file_io.write_xyz(
            xyz_path,
            np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
            ["H", "H"],
            title="H2",
        )
        artifact = StructureArtifact(path=xyz_path, elements=["H", "H"])
        assert artifact.role == StructureRole.MINIMUM
        with pytest.raises(ValueError, match="transition-state"):
            run_irc(artifact)

    def test_irc_backend_failure(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        """Backend raises RuntimeError → result is failed."""
        ts = _ts_artifact(tmp_path)
        fake_backend.fail_next("irc", RuntimeError("ORCA IRC crashed"))

        result = run_irc(
            ts,
            resources={
                "output_dir": str(tmp_path / "irc_work"),
                "result_dir": str(tmp_path / "RESULT"),
            },
        )
        assert result.status == "failed"
        assert any("ORCA IRC crashed" in e for e in result.errors)


class TestIrcWorkflowAdapter:
    """Standalone workflow adapter behavior and output contract."""

    def test_workflow_adapter_writes_report_and_manifest(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        # Given: a tagged transition-state artifact and a fake IRC capability.
        ts = _ts_artifact(tmp_path)
        output_root = tmp_path / "irc_output"
        _fake_irc_with_endpoints(
            tmp_path,
            fake_backend,
            output_dir=output_root / "WORK" / "07_PATH" / "ORCA",
        )

        # When: the standalone IRC workflow runs.
        result = run_irc_workflow(
            ts,
            directions=("forward", "reverse"),
            output_dir=output_root,
            method="r2SCAN-3c",
            maxpoints=25,
            step=0.1,
        )

        # Then: the adapter returns a completed one-stage workflow result.
        assert result.status == "completed"
        assert result.stages_completed == ["irc"]
        assert result.metadata["output_dir"] == str(output_root)

        # And: the report and endpoint products are registered in RESULT.
        irc_dir = output_root / "RESULT" / "irc"
        assert (irc_dir / "irc_forward.xyz").is_file()
        assert (irc_dir / "irc_reverse.xyz").is_file()
        assert (irc_dir / "irc_report.json").is_file()
        manifest = ResultManifest.read(output_root / "RESULT")
        assert {product.kind for product in manifest.products} >= {
            ProductKind.IRC_ENDPOINT,
            ProductKind.REPORT,
        }
        checkpoint = json.loads(
            (output_root / "WORK" / "00_RUNTIME" / "checkpoint.json").read_text(encoding="utf-8")
        )
        assert checkpoint["workflow"] == "irc"
        assert checkpoint["step_states"][0]["status"] == "completed"

    def test_workflow_adapter_accepts_ts_tag_when_role_is_unspecified(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        # Given: an artifact whose XYZ comment carries the TS role.
        ts = _ts_artifact(tmp_path, role=StructureRole.MINIMUM)
        coordinates, symbols = file_io.read_xyz(ts.path)
        file_io.write_xyz(ts.path, coordinates, symbols, title="TAG: TS | candidate_id=ts_001")
        output_root = tmp_path / "tagged_output"
        _fake_irc_with_endpoints(
            tmp_path,
            fake_backend,
            output_dir=output_root / "WORK" / "07_PATH" / "ORCA",
        )

        # When: the adapter infers the role from the input TAG comment.
        result = run_irc_workflow(ts, output_dir=output_root)

        # Then: the tagged transition state is accepted.
        assert result.status == "completed"

    def test_workflow_adapter_rejects_non_ts_before_backend(
        self,
        tmp_path: Path,
        fake_backend: Any,
    ) -> None:
        # Given: a minimum artifact and an otherwise available backend.
        minimum = _ts_artifact(tmp_path, role=StructureRole.MINIMUM)

        # When / Then: role validation stops the workflow before QC dispatch.
        with pytest.raises(ValueError, match="transition-state"):
            run_irc_workflow(minimum, output_dir=tmp_path / "rejected")
        assert fake_backend.calls == []


def test_irc_cli_without_role_returns_usage_error(tmp_path: Path) -> None:
    # Given: a plain XYZ input with no transition-state declaration.
    input_path = tmp_path / "plain.xyz"
    input_path.write_text("2\nplain\nH 0 0 0\nH 0 0 0.7\n", encoding="utf-8")

    # When: the IRC CLI is invoked without --input-role.
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "run",
            "irc",
            "--input",
            str(input_path),
            "--log-level",
            "ERROR",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the boundary rejects the ambiguous role with usage status 2.
    assert completed.returncode == 2
    assert "--input-role transition_state" in f"{completed.stdout}\n{completed.stderr}"


def test_irc_registry_entry_is_available() -> None:
    # Given: the workflow registry used by API and scheduler introspection.
    entry = get_workflow_entry("irc")

    # When / Then: IRC is registered as an ORCA-backed workflow.
    assert entry is not None
    assert entry.name == "irc"
    assert "orca" in entry.requires_binaries


def test_irc_stage_plan_is_registered() -> None:
    # Given: a scheduler JobSpec for the standalone IRC workflow.
    spec = JobSpec(workflow="irc", input={"input_role": "transition_state"})

    # When: stage planning resolves the workflow.
    plan = get_stage_plan(spec)

    # Then: exactly one IRC stage is planned.
    assert [stage.stage_name for stage in plan] == ["irc"]


def test_irc_scheduler_commands_forward_request_options(tmp_path: Path) -> None:
    # Given: a scheduler IRC request with explicit role, direction, and controls.
    spec = JobSpec(
        workflow="irc",
        name="ts-task",
        input={
            "input_artifact": "inputs/ts.xyz",
            "input_role": "transition_state",
            "directions": ["forward"],
        },
        method={"method": "M062X", "basis": "def2-SVP", "maxpoints": 33, "step": 0.2},
        resources={"nproc": 4, "mem": "2GB"},
    )

    # When: local and remote scheduler command builders translate the request.
    local = JobRunner(python_executable="python")._build_cmd(
        spec, tmp_path, input_path="inputs/ts.xyz"
    )
    remote = build_remote_cli_command(spec, input_path="inputs/ts.xyz", python_executable="python")

    # Then: both command forms retain the independent IRC options.
    for command in (local, remote):
        assert command[:5] == ["python", "-m", "acp.cli", "run", "irc"]
        assert command[command.index("--input-role") + 1] == "transition_state"
        assert command[command.index("--direction") + 1] == "forward"
        assert command[command.index("--method") + 1] == "M062X"
        assert command[command.index("--basis") + 1] == "def2-SVP"
        assert command[command.index("--maxpoints") + 1] == "33"
        assert command[command.index("--step") + 1] == "0.2"


def test_irc_scheduler_commands_preserve_both_directions(tmp_path: Path) -> None:
    spec = JobSpec(
        workflow="irc",
        input={
            "input_artifact": "inputs/ts.xyz",
            "input_role": "transition_state",
            "directions": ["forward", "reverse"],
        },
        method={},
    )

    local = JobRunner(python_executable="python")._build_cmd(
        spec, tmp_path, input_path="inputs/ts.xyz"
    )
    remote = build_remote_cli_command(spec, input_path="inputs/ts.xyz", python_executable="python")

    assert local[local.index("--direction") + 1] == "both"
    assert remote[remote.index("--direction") + 1] == "both"


def test_simple_and_package_exports_alias_primitive() -> None:
    from acp.workflows import run_irc as package_run_irc

    assert simple_run_irc is primitive_run_irc
    assert package_run_irc is primitive_run_irc


def test_irc_request_is_not_a_calculation_plan_step(tmp_path: Path) -> None:
    request = build_irc_request(_ts_artifact(tmp_path), ("forward", "reverse"))

    assert isinstance(request, IrcRequest)
    assert not isinstance(request, CalculationPlan)
    assert request.directions == ("forward", "reverse")


# ---------------------------------------------------------------------------
# Validation module tests
# ---------------------------------------------------------------------------


class TestIrcValidation:
    """Tests for the migrated endpoint validation algorithms."""

    def test_perceive_connectivity_water(self) -> None:
        """Water (H2O) → O-H bonds detected."""
        from acp.calculations.irc.validation import perceive_connectivity

        symbols = ["O", "H", "H"]
        coords = np.array([[0.0, 0.0, 0.0], [0.96, 0.0, 0.0], [-0.24, 0.93, 0.0]])
        edges = perceive_connectivity(symbols, coords)
        assert (0, 1) in edges
        assert (0, 2) in edges
        assert (1, 2) not in edges  # H-H too far

    def test_connectivity_fingerprint_stable(self) -> None:
        """Same geometry → same fingerprint."""
        from acp.calculations.irc.validation import connectivity_fingerprint

        symbols = ["C", "C", "H"]
        coords = np.array([[0.0, 0.0, 0.0], [1.34, 0.0, 0.0], [1.0, 0.9, 0.0]])
        fp1 = connectivity_fingerprint(symbols, coords)
        fp2 = connectivity_fingerprint(symbols, coords)
        assert fp1 == fp2

    def test_mapped_heavy_atom_rmsd_identical(self) -> None:
        """Identical geometries → RMSD ≈ 0."""
        from acp.calculations.irc.validation import mapped_heavy_atom_rmsd

        coords = np.array([[0.0, 0.0, 0.0], [1.5, 0.0, 0.0], [0.0, 1.0, 0.0]])
        symbols = ["C", "C", "O"]
        pairs = [(0, 0), (1, 1), (2, 2)]
        rmsd = mapped_heavy_atom_rmsd(coords, symbols, coords, symbols, pairs)
        assert rmsd < 1e-10

    def test_classify_ts_identity_valid(self) -> None:
        """Exactly one imaginary frequency below cutoff → valid TS."""
        from acp.calculations.irc.validation import classify_ts_identity

        identity = classify_ts_identity([-800.0, 100.0, 200.0])
        assert identity.valid is True
        assert identity.imaginary_count == 1
        assert identity.imaginary_frequency_cm1 == -800.0

    def test_classify_ts_identity_no_imaginary(self) -> None:
        """No imaginary frequencies → not valid."""
        from acp.calculations.irc.validation import classify_ts_identity

        identity = classify_ts_identity([100.0, 200.0])
        assert identity.valid is False
        assert identity.imaginary_count == 0

    def test_classify_ts_identity_multiple_imaginary(self) -> None:
        """Multiple imaginary frequencies → not valid."""
        from acp.calculations.irc.validation import classify_ts_identity

        identity = classify_ts_identity([-800.0, -200.0, 100.0])
        assert identity.valid is False
        assert identity.imaginary_count == 2

    def test_classify_endpoint_geometry_match(self) -> None:
        """Nearly identical geometry → MATCH verdict."""
        from acp.calculations.irc.validation import classify_endpoint_geometry

        symbols = ["C", "C", "H", "H"]
        coords_ref = np.array([[0.0, 0.0, 0.0], [1.34, 0.0, 0.0], [0.5, 0.9, 0.0], [1.5, 0.9, 0.0]])
        coords_cand = coords_ref + 0.01  # tiny displacement
        result = classify_endpoint_geometry(symbols, coords_cand, symbols, coords_ref)
        assert result.verdict == "MATCH"
        assert result.rmsd_to_reference is not None
        assert result.rmsd_to_reference < 0.05

    def test_classify_endpoint_geometry_different(self) -> None:
        """Very different geometry → DIFFERENT verdict."""
        from acp.calculations.irc.validation import classify_endpoint_geometry

        symbols = ["C", "C", "H", "H"]
        coords_ref = np.array([[0.0, 0.0, 0.0], [1.34, 0.0, 0.0], [0.5, 0.9, 0.0], [1.5, 0.9, 0.0]])
        coords_cand = np.array([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.5, 2.0, 0.0], [3.5, 2.0, 0.0]])
        result = classify_endpoint_geometry(symbols, coords_cand, symbols, coords_ref)
        assert result.verdict == "DIFFERENT"
