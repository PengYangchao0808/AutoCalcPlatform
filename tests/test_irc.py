"""Tests for the IRC calculation primitive and endpoint validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from acp.calculations.contracts import StructureArtifact, StructureRole
from acp.calculations.primitives.irc import run_irc
from acp.storage.manifest import ProductKind, ResultManifest
from cccp.utils import file_io


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts_artifact(tmp_path: Path, *, role: StructureRole = StructureRole.TRANSITION_STATE) -> StructureArtifact:
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
) -> None:
    """Configure the fake backend to create IRC endpoint files and return them."""
    output_dir = tmp_path / "irc_work"
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
            resources={"output_dir": str(tmp_path / "irc_work"), "result_dir": str(tmp_path / "RESULT")},
        )

        # Result status
        assert result.status == "completed", f"Expected completed, got {result.status}: {result.errors}"
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
            resources={"output_dir": str(tmp_path / "irc_work"), "result_dir": str(tmp_path / "RESULT")},
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

        result = run_irc(ts, resources={"output_dir": str(tmp_path / "irc_work"), "result_dir": str(tmp_path / "RESULT")})
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
            resources={"output_dir": str(tmp_path / "irc_work"), "result_dir": str(tmp_path / "RESULT")},
        )
        assert result.status == "failed"
        assert any("ORCA IRC crashed" in e for e in result.errors)


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
