"""Tests for Molclus and ISOSTAT ACP backends."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from acp.backends import IsostatBackend, MolclusBackend, supports
from acp.backends.base import ClusteringTool, ConformerSearcher
from acp.backends.registry import get_backend


def _make_config() -> dict[str, object]:
    return {
        "executables": {
            "molclus": {"path": "molclus", "isostat_path": "isostat"},
            "xtb": {"path": "xtb"},
            "isostat": {"path": "isostat"},
        },
        "resources": {"nproc": 2},
        "theory": {"preoptimization": {"gfn_level": 0}},
    }


def _write_xyz(path: Path, comment: str = "Frame 0 | Energy: -1.0000000000") -> None:
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


def _write_multiframe_xyz(path: Path) -> None:
    _ = path.write_text(
        "\n".join(
            [
                "2",
                "Frame 0 | Energy: -1.0000000000",
                "H 0.0000000000 0.0000000000 0.0000000000",
                "H 0.0000000000 0.0000000000 0.7400000000",
                "2",
                "Frame 1 | Energy: -0.9000000000",
                "H 0.0000000000 0.0000000000 0.1000000000",
                "H 0.0000000000 0.0000000000 0.8400000000",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_molclus_backend_instantiation() -> None:
    backend = MolclusBackend(_make_config())

    assert backend.name == "molclus"
    assert backend.molclus_path == "molclus"
    assert backend.xtb_path == "xtb"
    assert backend.isostat_path == "isostat"
    assert isinstance(backend, ConformerSearcher)
    assert get_backend("molclus") is MolclusBackend
    assert supports("molclus", "conformer_search") is True


def test_molclus_is_available_with_mock() -> None:
    backend = MolclusBackend(_make_config())

    with patch("acp.backends.molclus_backend.shutil.which", return_value="/usr/bin/molclus") as mock_which:
        assert backend.is_available() is True

    mock_which.assert_called_once_with("molclus")


def test_molclus_search_mocked(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    output_dir = tmp_path / "molclus_run"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert timeout > 0
        assert check is True

        executable = Path(cmd[0]).name
        if executable == "xtb":
            _write_multiframe_xyz(cwd / "xtb.trj")
        elif executable == "molclus":
            _write_multiframe_xyz(cwd / "isomers.xyz")
        elif executable == "isostat":
            _write_multiframe_xyz(cwd / "cluster.xyz")
        else:
            raise AssertionError(f"Unexpected executable: {cmd[0]}")

        return subprocess.CompletedProcess(cmd, 0, stdout=f"ran {executable}", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run) as mock_run:
        result = backend.search(initial_xyz, output_dir=output_dir, charge=1, multiplicity=2, nout=1)

    assert result.success is True
    assert result.converged is True
    assert result.output_file == output_dir / "cluster.xyz"
    assert result.coordinates is not None
    assert result.coordinates.shape == (4, 3)
    assert result.symbols == ["H", "H"]
    assert (output_dir / "traj.xyz").exists()
    assert (output_dir / "md.inp").exists()
    assert (output_dir / "settings.ini").exists()
    assert mock_run.call_count == 3


def test_isostat_backend_instantiation() -> None:
    backend = IsostatBackend(_make_config())

    assert backend.name == "isostat"
    assert backend.isostat_path == "isostat"
    assert isinstance(backend, ClusteringTool)
    assert get_backend("isostat") is IsostatBackend
    assert supports("isostat", "clustering") is True


def test_isostat_cluster_mocked(tmp_path: Path) -> None:
    backend = IsostatBackend(_make_config())
    ensemble_xyz = tmp_path / "ensemble.xyz"
    output_dir = tmp_path / "cluster_run"
    _write_multiframe_xyz(ensemble_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert Path(cmd[0]).name == "isostat"
        assert capture_output is True
        assert text is True
        assert timeout > 0
        assert check is True
        _write_xyz(cwd / "cluster.xyz")
        return subprocess.CompletedProcess(cmd, 0, stdout="clustered", stderr="")

    with patch("acp.backends.isostat_backend.subprocess.run", side_effect=_mock_run) as mock_run:
        result = backend.cluster(ensemble_xyz, output_dir=output_dir, edis=0.6, gdis=0.3, nout=1, nthreads=4)

    assert result.success is True
    assert result.converged is True
    assert result.output_file == output_dir / "cluster.xyz"
    assert result.coordinates is not None
    assert result.coordinates.shape == (2, 3)
    assert result.symbols == ["H", "H"]
    mock_run.assert_called_once()
