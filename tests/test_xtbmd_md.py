"""Tests for the workflow-layer multi-replica xTB-MD sampling convention.

Covers ``run_md_replicas`` (seed increments, distinct RDKit multi-start
conformations per replica, trajectory merge) and the underlying
``enumerate_embeddings`` multi-start enumeration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from acp.backends.base import QCResult
from acp.chem.embedding import enumerate_embeddings
from acp.workflows.xtbmd_md import run_md_replicas


def _write_single_frame(path: Path, comment: str = "input") -> None:
    _ = path.write_text(
        "\n".join(
            [
                "2",
                comment,
                "H 0.0000000000 0.0000000000 0.0000000000",
                "H 0.0000000000 0.0000000000 0.7400000000",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_water_frame(path: Path, comment: str = "input") -> None:
    _ = path.write_text(
        "\n".join(
            [
                "3",
                comment,
                "O 0.0000000000 0.0000000000 0.1173000000",
                "H 0.0000000000 0.7572000000 -0.4692000000",
                "H 0.0000000000 -0.7572000000 -0.4692000000",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_trajectory(path: Path, frames: int, base_energy: float = -10.0) -> None:
    _ = path.write_text(
        "\n".join(
            [
                block
                for frame in range(frames)
                for block in (
                    "2",
                    f"Frame {frame} | Energy: {base_energy + frame * 0.1:.10f}",
                    "H 0.0000000000 0.0000000000 0.0000000000",
                    "H 0.0000000000 0.0000000000 0.7400000000",
                    "",
                )
            ]
        ),
        encoding="utf-8",
    )


def _ok_result(n_frames: int, traj: Path) -> QCResult:
    return QCResult(
        success=True,
        converged=True,
        output_file=traj,
        metadata={"trajectory_file": str(traj), "n_frames": n_frames},
    )


def _mock_backend(write_traj: bool = True) -> MagicMock:
    backend = MagicMock()

    def _run_md(initial_xyz: Path, *, output_dir: Path, seed: int, **kwargs: object) -> QCResult:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        if write_traj:
            _write_trajectory(out / "traj.xyz", 3, base_energy=-10.0 + seed * 0.01)
            return _ok_result(3, out / "traj.xyz")
        return QCResult(success=False, error_message="boom")

    backend.run_md.side_effect = _run_md
    return backend


def _backend_factory(backend: MagicMock) -> Any:
    """Return a ``get_backend("molclus")``-style factory recording the
    constructor kwargs it was called with."""

    def factory(config: dict[str, object], **kwargs: object) -> MagicMock:
        backend.ctor_kwargs = dict(kwargs)
        return backend

    return factory


# ---------------------------------------------------------------------------
# Single replica
# ---------------------------------------------------------------------------


def test_single_replica_uses_primary_xyz_and_base_seed(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch("acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)):
        result = run_md_replicas(
            "CCO",
            primary,
            md_seed=42,
            md_seeds=1,
            output_dir=tmp_path / "out",
            temperature=400.0,
            time_ps=100.0,
        )

    assert result.success is True
    assert backend.run_md.call_count == 1
    call = backend.run_md.call_args
    assert call.args[0] == primary  # single replica: primary embedding as-is
    assert call.kwargs["seed"] == 42
    assert call.kwargs["md_method"] == "gfnff"
    assert call.kwargs["temperature"] == 400.0
    assert call.kwargs["time_ps"] == 100.0
    assert call.kwargs["solvent"] is None

    assert result.metadata["md_seed"] == 42
    assert result.metadata["md_seeds"] == 1
    assert result.metadata["start_conf_index"] == [0]
    assert result.metadata["replica_frames"] == [3]
    assert result.metadata["n_frames"] == 3
    assert Path(result.metadata["trajectory_file"]).exists()


# ---------------------------------------------------------------------------
# Multi-replica: seed increments + distinct RDKit multi-start
# ---------------------------------------------------------------------------


def test_multi_replica_seeds_increment_and_starts_differ(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch("acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)):
        result = run_md_replicas(
            "CCO",
            primary,
            md_seed=42,
            md_seeds=3,
            output_dir=tmp_path / "out",
        )

    assert result.success is True
    assert backend.run_md.call_count == 3
    seeds = [call.kwargs["seed"] for call in backend.run_md.call_args_list]
    assert seeds == [42, 43, 44]

    start_files = [call.args[0] for call in backend.run_md.call_args_list]
    assert len({str(path) for path in start_files}) == 3, "replica start files must be distinct"
    for start_file in start_files:
        assert Path(start_file).exists()
        assert Path(start_file).parent.name.startswith("replica_")

    # v1.3: replicas start from distinct RDKit embeddings of the original
    # molecule — the embedded XYZ blocks differ.
    blocks = [Path(path).read_text(encoding="utf-8") for path in start_files]
    assert len(set(blocks)) == 3, "multi-start conformations must differ"

    # Merged trajectory frame count = sum of per-replica frame counts.
    assert result.metadata["start_conf_index"] == [0, 1, 2]
    assert result.metadata["replica_frames"] == [3, 3, 3]
    assert result.metadata["n_frames"] == 9

    merged = Path(result.metadata["trajectory_file"])
    assert merged.exists()
    merged_text = merged.read_text(encoding="utf-8")
    assert merged_text.count("| Energy:") == 9  # 3 replicas × 3 frames
    assert "Energy:" in merged_text  # frame titles (GFN-FF energies) preserved


def test_multi_replica_merge_preserves_titles(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch("acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)):
        result = run_md_replicas(
            "CCO",
            primary,
            md_seed=10,
            md_seeds=2,
            output_dir=tmp_path / "out",
        )

    merged_text = Path(result.metadata["trajectory_file"]).read_text(encoding="utf-8")
    assert "Energy: -9.9000000000" in merged_text  # replica 0 frame energies
    assert "Energy: -9.8000000000" in merged_text


def test_multi_replica_failure_fails_fast(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = MagicMock()
    calls = {"n": 0}

    def _run_md(initial_xyz: Path, *, output_dir: Path, seed: int, **kwargs: object) -> QCResult:
        calls["n"] += 1
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        if calls["n"] == 2:
            return QCResult(success=False, error_message="boom", log_file=out / "xtb_md.log")
        _write_trajectory(out / "traj.xyz", 3)
        return _ok_result(3, out / "traj.xyz")

    backend.run_md.side_effect = _run_md

    with patch("acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)):
        result = run_md_replicas(
            "CCO",
            primary,
            md_seed=42,
            md_seeds=3,
            output_dir=tmp_path / "out",
        )

    assert result.success is False
    assert "replica 2" in (result.error_message or "")
    assert "seed=43" in (result.error_message or "")


# ---------------------------------------------------------------------------
# Robustness fixes
# ---------------------------------------------------------------------------


def test_run_md_replicas_forwards_timeout_to_backend(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch(
        "acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)
    ) as mock_get:
        result = run_md_replicas(
            "CCO",
            primary,
            output_dir=tmp_path / "out",
            timeout=3600,
        )

    assert result.success is True
    mock_get.assert_called_once_with("molclus")
    assert backend.ctor_kwargs == {"timeout": 3600}


def test_run_md_replicas_default_no_timeout_kwarg(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch(
        "acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)
    ):
        result = run_md_replicas("CCO", primary, output_dir=tmp_path / "out")

    assert result.success is True
    assert backend.ctor_kwargs == {}


def test_run_md_replicas_rejects_invalid_seed_count(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)

    for bad in (0, -2, 2.5, True):
        with pytest.raises(ValueError):
            run_md_replicas(
                "CCO",
                primary,
                md_seeds=bad,
                output_dir=tmp_path / "out",
            )

    for bad in (-1, 42.5, True):
        with pytest.raises(ValueError):
            run_md_replicas(
                "CCO",
                primary,
                md_seed=bad,
                output_dir=tmp_path / "out",
            )


def test_run_md_replicas_zero_timeout_means_backend_default(tmp_path: Path) -> None:
    primary = tmp_path / "molecule.xyz"
    _write_single_frame(primary)
    backend = _mock_backend()

    with patch(
        "acp.workflows.xtbmd_md.get_backend", return_value=_backend_factory(backend)
    ):
        result = run_md_replicas(
            "CCO",
            primary,
            output_dir=tmp_path / "out",
            timeout=0,
        )

    assert result.success is True
    assert backend.ctor_kwargs == {}


def test_merge_rejects_atom_count_mismatch(tmp_path: Path) -> None:
    from acp.workflows.xtbmd_md import _merge_trajectories

    traj_a = tmp_path / "a.xyz"
    traj_b = tmp_path / "b.xyz"
    _write_trajectory(traj_a, 2)
    _ = traj_b.write_text(
        "\n".join(["3", "Frame 0", "O 0 0 0", "H 0 0 0.9", "H 0 0 -0.9", ""]),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Atom count mismatch"):
        _merge_trajectories([traj_a, traj_b], tmp_path / "merged.xyz")


def test_merge_rejects_malformed_frame(tmp_path: Path) -> None:
    from acp.workflows.xtbmd_md import _merge_trajectories

    traj_a = tmp_path / "a.xyz"
    _ = traj_a.write_text("2\nFrame 0\nH 0 0 0\nH 0 0 0.7\nGARBAGE\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Malformed trajectory frame"):
        _merge_trajectories([traj_a], tmp_path / "merged.xyz")


def test_merge_rejects_truncated_frame(tmp_path: Path) -> None:
    from acp.workflows.xtbmd_md import _merge_trajectories

    traj_a = tmp_path / "a.xyz"
    _ = traj_a.write_text("2\nFrame 0\nH 0 0 0\nH 0 0 0.7\n2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Truncated trajectory frame"):
        _merge_trajectories([traj_a], tmp_path / "merged.xyz")


def test_enumerate_embeddings_rejects_gjf(tmp_path: Path) -> None:
    gjf = tmp_path / "input.gjf"
    _ = gjf.write_text("#p wb97xd/def2tzvp\n\n0 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported structure format"):
        enumerate_embeddings(gjf, n=2)


# ---------------------------------------------------------------------------
# enumerate_embeddings (multi-start enumeration)
# ---------------------------------------------------------------------------


def test_enumerate_embeddings_smiles_produces_distinct_structures() -> None:
    blocks = enumerate_embeddings("CCCC", n=4, seed_base=42)
    assert len(blocks) == 4
    assert len(set(blocks)) == 4, "embeddings must be distinct"
    for block in blocks:
        lines = block.strip("\n").splitlines()
        assert lines[0].strip() == "14"  # C4H10 → 14 atoms
        assert "emb=" in lines[1]


def test_enumerate_embeddings_xyz_file_input(tmp_path: Path) -> None:
    xyz = tmp_path / "input.xyz"
    _write_water_frame(xyz)
    blocks = enumerate_embeddings(xyz, n=2, seed_base=1)
    assert len(blocks) == 2
    assert len(set(blocks)) == 2


def test_enumerate_embeddings_invalid_input() -> None:
    with pytest.raises(ValueError):
        enumerate_embeddings("not-a-smiles-!!!", n=2)
    with pytest.raises(ValueError):
        enumerate_embeddings("CCO", n=0)
