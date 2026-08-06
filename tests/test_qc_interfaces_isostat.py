"""Tests for the cccp ISOSTAT legacy QC interface."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from cccp.qc.interfaces.isostat import (
    IsostatInterface,
    _normalise_titles_for_isostat,
)

CONFIG = {
    "executables": {"isostat": {"path": "isostat"}},
    "resources": {"nproc": 1},
}


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


def test_interface_instantiates_with_minimal_config() -> None:
    interface = IsostatInterface(CONFIG)
    assert interface.exe_path == "isostat"
    assert interface.timeout == 300


def test_interface_reads_molclus_isostat_path_fallback() -> None:
    config = {
        "executables": {"molclus": {"isostat_path": "/opt/molclus/isostat"}},
        "resources": {"nproc": 1},
    }
    interface = IsostatInterface(config)
    assert interface.exe_path == "/opt/molclus/isostat"


def test_normalise_titles_to_molclus_bare_energy(tmp_path: Path) -> None:
    """ISOSTAT rejects "Frame N | Energy: X" titles (exit 24 on this
    Fortran build); the interface must rewrite them as Molclus bare-energy
    lines before invoking ISOSTAT (curcusone-test failure root cause)."""
    src = tmp_path / "isomers.xyz"
    src.write_text(
        "2\n"
        "Frame 0 | Energy: -64.6127037805\n"
        "O  0.0  0.0  0.0\n"
        "O  1.0  0.0  0.0\n"
        "2\n"
        "Frame 1 | Energy: -64.6126000000\n"
        "O  0.0  0.0  0.0\n"
        "O  1.0  0.0  0.0\n",
        encoding="utf-8",
    )

    out = _normalise_titles_for_isostat(src)
    try:
        text = out.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[1] == "        -64.6127037805"
        assert lines[5] == "        -64.6126000000"
        # Coordinates are untouched; no "Frame N | Energy:" remains.
        assert "Frame" not in text
        assert "O  0.0  0.0  0.0" in text
        # The original file is never mutated.
        assert "Frame 0 | Energy" in src.read_text(encoding="utf-8")
    finally:
        out.unlink(missing_ok=True)


def test_normalise_titles_keeps_coord_lines(tmp_path: Path) -> None:
    """Multi-frame inputs with blank-line separation and no-energy titles
    must survive normalisation (frames without a float keep their title)."""
    src = tmp_path / "mixed.xyz"
    src.write_text(
        "1\nbare  -1.5\nH  0.0  0.0  0.0\n\n1\nno energy here\nH  1.0  0.0  0.0\n",
        encoding="utf-8",
    )
    out = _normalise_titles_for_isostat(src)
    try:
        lines = out.read_text(encoding="utf-8").splitlines()
        assert lines[1] == "        -1.5000000000"
        # Frame 2 title has no float → kept verbatim; coordinates intact.
        assert lines[4] == "no energy here"
        assert lines[5] == "H  1.0  0.0  0.0"
    finally:
        out.unlink(missing_ok=True)


def test_cluster_success(tmp_path: Path) -> None:
    interface = IsostatInterface(CONFIG)
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
        env: dict[str, str] | None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        assert Path(cmd[0]).name == "isostat"
        assert check is True
        assert timeout == 300
        assert env is not None
        assert env["OMP_NUM_THREADS"] == "2"
        assert env["MKL_NUM_THREADS"] == "2"
        assert env["OPENBLAS_NUM_THREADS"] == "2"
        # ISOSTAT prompts for the cluster-count when there are many clusters;
        # feeding ENTER (a bare newline) keeps all clusters and avoids an
        # end-of-file crash in non-interactive (subprocess) mode.
        assert input == "\n"
        # The input passed to ISOSTAT must be the normalised temp file.
        assert "_isostat_" in cmd[1]
        (cwd / "cluster.xyz").write_text(
            "2\n        -1.0000000000\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="clustered", stderr="")

    with patch("cccp.qc.interfaces.isostat.subprocess.run", side_effect=_mock_run):
        result = interface.cluster(
            ensemble_xyz,
            output_dir,
            edis=0.6,
            gdis=0.3,
            nout=1,
            nthreads=2,
        )

    assert result.success is True
    assert result.converged is True
    assert result.output_file == output_dir / "cluster.xyz"
    assert result.coordinates is not None
    assert result.coordinates.shape == (2, 3)
    assert result.symbols == ["H", "H"]
    assert result.log_file == output_dir / "isostat.log"
    # Temp input cleaned up by the finally block.
    assert not list(output_dir.glob("*_isostat_*.xyz"))
    assert (output_dir / "isostat.log").exists()


def test_cluster_feeds_stdin_to_satisfy_cluster_count_prompt(
    tmp_path: Path,
) -> None:
    """When ISOSTAT finds many clusters it prompts interactively ("press ENTER
    to keep all, or input a number").  In non-interactive subprocess mode that
    read hits EOF and the Fortran runtime aborts with ``severe (24): end-of-file
    during read`` (exit 24).  The interface must feed a bare newline (ENTER =
    keep all clusters) on stdin so the prompt is answered and ISOSTAT exits 0.
    """
    interface = IsostatInterface(CONFIG)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    _write_multiframe_xyz(ensemble_xyz)

    captured: dict[str, object] = {}

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        env: dict[str, str] | None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        captured["input"] = input
        (cwd / "cluster.xyz").write_text(
            "2\n        -1.0000000000\nH 0.0 0.0 0.0\nH 0.0 0.0 0.74\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="clustered", stderr="")

    with patch("cccp.qc.interfaces.isostat.subprocess.run", side_effect=_mock_run):
        result = interface.cluster(ensemble_xyz, tmp_path)

    assert result.success is True
    assert captured["input"] == "\n"


def test_cluster_exit_24_classified_as_failure(tmp_path: Path) -> None:
    """Non-zero exits (e.g. ISOSTAT exit 24) must surface as classified
    QCResult failures instead of the legacy silent (path, []) return."""
    interface = IsostatInterface(CONFIG)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    _write_multiframe_xyz(ensemble_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        env: dict[str, str] | None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(
            returncode=24, cmd=cmd, output="", stderr="Unable to load energy"
        )

    with patch("cccp.qc.interfaces.isostat.subprocess.run", side_effect=_mock_run):
        result = interface.cluster(ensemble_xyz, tmp_path)

    assert result.success is False
    assert "exit code 24" in (result.error_message or "")
    assert result.output_file is None


def test_cluster_timeout_classified_as_failure(tmp_path: Path) -> None:
    interface = IsostatInterface(CONFIG, timeout=10)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    _write_multiframe_xyz(ensemble_xyz)

    def _mock_run(
        cmd: list[str],
        cwd: Path,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
        env: dict[str, str] | None,
        input: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    with patch("cccp.qc.interfaces.isostat.subprocess.run", side_effect=_mock_run):
        result = interface.cluster(ensemble_xyz, tmp_path)

    assert result.success is False
    assert "timed out" in (result.error_message or "")
    assert (tmp_path / "isostat.log").exists()


def test_cluster_oserror_classified_as_failure(tmp_path: Path) -> None:
    interface = IsostatInterface(CONFIG)
    ensemble_xyz = tmp_path / "ensemble.xyz"
    _write_multiframe_xyz(ensemble_xyz)

    with patch(
        "cccp.qc.interfaces.isostat.subprocess.run",
        side_effect=OSError("isostat not executable"),
    ):
        result = interface.cluster(ensemble_xyz, tmp_path)

    assert result.success is False
    assert "execution failed" in (result.error_message or "")
