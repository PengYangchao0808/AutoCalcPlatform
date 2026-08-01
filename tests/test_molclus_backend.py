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


def _write_trajectory(path: Path, frames: int, comment: str = "Frame | Energy: -10.0") -> None:
    _ = path.write_text(
        "\n".join(
            [
                block
                for frame in range(frames)
                for block in ("2", comment, "H 0 0 0", "H 0 0 0.74", "")
            ]
        ),
        encoding="utf-8",
    )


def test_molclus_run_md_gfnff_command_and_seed(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    output_dir = tmp_path / "md_run"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert Path(cmd[0]).name == "xtb"
        assert capture_output is True and text is True and check is True
        assert "--gfnff" in cmd
        assert "--seed" in cmd and "42" in cmd
        assert "--omd" in cmd
        assert "--alpb" in cmd and "water" in cmd
        assert "--chrg" in cmd and "1" in cmd
        assert "--uhf" in cmd and "1" in cmd
        assert "--input" in cmd and "md.inp" in cmd
        _write_trajectory(cwd / "xtb.trj", 60)
        return subprocess.CompletedProcess(cmd, 0, stdout="md ok", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run) as mock_run:
        result = backend.run_md(
            initial_xyz,
            output_dir=output_dir,
            md_method="gfnff",
            seed=42,
            solvent="water",
            solvent_model="alpb",
            charge=1,
            multiplicity=2,
        )

    assert result.success is True
    assert result.converged is True
    assert result.output_file == output_dir / "traj.xyz"
    assert result.metadata["n_frames"] == 60
    assert result.metadata["trajectory_file"] == str(output_dir / "traj.xyz")
    md_inp = (output_dir / "md.inp").read_text(encoding="utf-8")
    assert "seed=42" in md_inp
    assert "temp=400.0" in md_inp
    assert "time=100.0" in md_inp
    assert "nvt=true" in md_inp
    assert "shake=true" in md_inp
    assert (output_dir / "traj.xyz").exists()
    assert mock_run.call_count == 1


def test_molclus_run_md_gfn1_and_gbsa_solvent(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert "--gfn" in cmd and "1" in cmd
        assert "--gfnff" not in cmd
        assert "--gbsa" in cmd and "water" in cmd
        assert "--seed" in cmd and "7" in cmd
        _write_trajectory(cwd / "xtb.trj", 55)
        return subprocess.CompletedProcess(cmd, 0, stdout="md ok", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run):
        result = backend.run_md(
            initial_xyz,
            output_dir=tmp_path / "md_run2",
            md_method="gfn1",
            seed=7,
            solvent="water",
            solvent_model="gbsa",
        )

    assert result.success is True
    assert result.metadata["n_frames"] == 55


def test_molclus_run_md_gfn_level_fallback(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert "--gfn" in cmd and "2" in cmd
        assert "--gfnff" not in cmd
        _write_trajectory(cwd / "xtb.trj", 55)
        return subprocess.CompletedProcess(cmd, 0, stdout="md ok", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run):
        result = backend.run_md(
            initial_xyz,
            output_dir=tmp_path / "md_run3",
            md_method="",
            gfn_level=2,
        )

    assert result.success is True


def test_molclus_run_md_truncated_trajectory_fails_fast(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        _write_trajectory(cwd / "xtb.trj", 40)
        return subprocess.CompletedProcess(cmd, 0, stdout="md ok", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run):
        result = backend.run_md(initial_xyz, output_dir=tmp_path / "md_run4")

    assert result.success is False
    assert "invalid" in (result.error_message or "")
    assert "40" in (result.error_message or "")


def test_molclus_run_md_missing_trajectory_fails(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    _write_xyz(initial_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 0, stdout="md ok", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run):
        result = backend.run_md(initial_xyz, output_dir=tmp_path / "md_run5")

    assert result.success is False
    assert "xtb.trj" in (result.error_message or "")


def test_molclus_run_md_unknown_method_rejected(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    _write_xyz(initial_xyz)

    result = backend.run_md(
        initial_xyz,
        output_dir=tmp_path / "md_run6",
        md_method="bogus",
    )

    assert result.success is False
    assert "Unknown MD method" in (result.error_message or "")


def test_molclus_search_passes_seed_and_solvent(tmp_path: Path) -> None:
    backend = MolclusBackend(_make_config())
    initial_xyz = tmp_path / "input.xyz"
    output_dir = tmp_path / "molclus_run"
    _write_xyz(initial_xyz)

    seen_xtb_cmd: list[str] = []

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        executable = Path(cmd[0]).name
        if executable == "xtb":
            seen_xtb_cmd.extend(cmd)
            _write_multiframe_xyz(cwd / "xtb.trj")
        elif executable == "molclus":
            _write_multiframe_xyz(cwd / "isomers.xyz")
        elif executable == "isostat":
            _write_multiframe_xyz(cwd / "cluster.xyz")
        else:
            raise AssertionError(f"Unexpected executable: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 0, stdout=f"ran {executable}", stderr="")

    with patch("acp.backends.molclus_backend.subprocess.run", side_effect=_mock_run):
        result = backend.search(
            initial_xyz,
            output_dir=output_dir,
            md_method="gfnff",
            seed=42,
            solvent="water",
            solvent_model="alpb",
        )

    assert result.success is True
    assert "--gfnff" in seen_xtb_cmd
    assert "--seed" in seen_xtb_cmd and "42" in seen_xtb_cmd
    assert "--alpb" in seen_xtb_cmd and "water" in seen_xtb_cmd
    md_inp = (output_dir / "md.inp").read_text(encoding="utf-8")
    assert "seed=42" in md_inp
    settings_ini = (output_dir / "settings.ini").read_text(encoding="utf-8")
    assert "xtb_arg=--gfnff" in settings_ini


def test_molclus_is_available_with_mock() -> None:
    backend = MolclusBackend(_make_config())

    with patch(
        "acp.backends.molclus_backend.shutil.which", return_value="/usr/bin/molclus"
    ) as mock_which:
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
        result = backend.search(
            initial_xyz, output_dir=output_dir, charge=1, multiplicity=2, nout=1
        )

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
        result = backend.cluster(
            ensemble_xyz, output_dir=output_dir, edis=0.6, gdis=0.3, nout=1, nthreads=4
        )

    assert result.success is True
    assert result.converged is True
    assert result.output_file == output_dir / "cluster.xyz"
    assert result.coordinates is not None
    assert result.coordinates.shape == (2, 3)
    assert result.symbols == ["H", "H"]
    mock_run.assert_called_once()
