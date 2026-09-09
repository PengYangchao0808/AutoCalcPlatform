"""Tests for MPI runtime discovery + shell-environment sniffing (cccp.software)."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import cccp.software as software
from cccp.software import (
    ShellEnvironment,
    orca_runtime_env,
    resolve_mpirun,
    sniff_login_shell_env,
    sniff_rc_files,
)


def _make_executable(directory: Path, name: str = "mpirun") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _sniff_none(_timeout: float = 5.0) -> None:
    return None


@pytest.fixture(autouse=True)
def _hermetic_sniff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the sniff cache and disable real login-shell sniffing."""
    software._reset_shell_env_cache()
    monkeypatch.setattr(software, "sniff_login_shell_env", _sniff_none)


def test_sniff_rc_files_extracts_absolute_entries(tmp_path: Path) -> None:
    (tmp_path / ".bashrc").write_text(
        "\n".join(
            [
                "export PATH=/opt/ompi/bin:$PATH",
                'export LD_LIBRARY_PATH="/opt/ompi/lib:${LD_LIBRARY_PATH}"',
                "PATH=$HOME/.local/bin:$PATH",
                "# export PATH=/commented/bin:$PATH",
                "",
            ]
        ),
        encoding="utf-8",
    )

    env = sniff_rc_files(home=tmp_path)

    assert Path("/opt/ompi/bin") in env.path_dirs
    assert Path("/opt/ompi/lib") in env.ld_library_path_dirs
    assert all("$" not in str(d) for d in env.path_dirs)
    assert Path("/commented/bin") not in env.path_dirs
    assert env.source == "rc-files"


def test_sniff_rc_files_locates_mpirun(tmp_path: Path) -> None:
    mpirun = _make_executable(tmp_path / "ompi" / "bin")
    (tmp_path / ".bashrc").write_text(
        f"export PATH={tmp_path / 'ompi' / 'bin'}:$PATH\n", encoding="utf-8"
    )

    env = sniff_rc_files(home=tmp_path)

    assert env.mpirun == mpirun


def test_sniff_rc_files_missing_home_is_empty(tmp_path: Path) -> None:
    env = sniff_rc_files(home=tmp_path / "does-not-exist")

    assert env.path_dirs == ()
    assert env.mpirun is None


def test_resolve_mpirun_prefers_explicit_config(tmp_path: Path) -> None:
    pinned = _make_executable(tmp_path / "pinned" / "bin")
    other = _make_executable(tmp_path / "other" / "bin")
    monkey_sniff = ShellEnvironment(mpirun=other, source="login-shell")

    with (
        patch.object(software.shutil, "which", return_value=str(other)),
        patch.object(software, "sniff_login_shell_env", return_value=monkey_sniff),
    ):
        assert resolve_mpirun(pinned) == pinned


def test_resolve_mpirun_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pinned = _make_executable(tmp_path / "env-pinned" / "bin")
    monkeypatch.setenv(software.MPI_ENV_VAR, str(pinned))

    assert resolve_mpirun() == pinned


def test_resolve_mpirun_uses_login_shell_sniff(tmp_path: Path) -> None:
    sniffed = _make_executable(tmp_path / "ompi" / "bin")
    sniff_result = ShellEnvironment(mpirun=sniffed, source="login-shell")

    with (
        patch.object(software.shutil, "which", return_value=None),
        patch.object(software, "sniff_login_shell_env", return_value=sniff_result),
    ):
        assert resolve_mpirun() == sniffed


def test_resolve_mpirun_falls_back_to_rc_parse(tmp_path: Path) -> None:
    rc_mpirun = _make_executable(tmp_path / "ompi" / "bin")
    (tmp_path / ".bashrc").write_text(
        f"export PATH={tmp_path / 'ompi' / 'bin'}:$PATH\n", encoding="utf-8"
    )

    with (
        patch.object(software.shutil, "which", return_value=None),
        patch("pathlib.Path.home", return_value=tmp_path),
    ):
        assert resolve_mpirun() == rc_mpirun


def test_resolve_mpirun_glob_includes_orca_dir(tmp_path: Path) -> None:
    orca_dir = tmp_path / "orca611"
    orca_dir.mkdir()
    bundled = _make_executable(orca_dir / "openmpi" / "bin")

    with (
        patch.object(software.shutil, "which", return_value=None),
        patch.object(software, "sniff_login_shell_env", _sniff_none),
        patch.object(software.Path, "home", return_value=tmp_path / "nohome"),
    ):
        assert resolve_mpirun(orca_dir=orca_dir) == bundled


def test_orca_runtime_env_noop_when_mpi_already_on_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "ompi" / "bin"
    _make_executable(bin_dir)
    monkey_path = f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    with patch.dict(os.environ, {"PATH": monkey_path}):
        assert orca_runtime_env(None) is None


def test_orca_runtime_env_injects_explicit_mpi_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "ompi" / "bin"
    mpirun = _make_executable(bin_dir)
    lib_dir = tmp_path / "ompi" / "lib"
    lib_dir.mkdir()

    env = orca_runtime_env(None, mpi_path=mpirun)

    assert env is not None
    assert env["PATH"].split(os.pathsep)[0] == str(bin_dir)
    assert env["LD_LIBRARY_PATH"].split(os.pathsep)[0] == str(lib_dir)


def test_orca_runtime_env_without_lib_sibling(tmp_path: Path) -> None:
    mpirun = _make_executable(tmp_path / "ompi" / "bin")

    env = orca_runtime_env(None, mpi_path=mpirun)

    assert env is not None
    assert env["PATH"].split(os.pathsep)[0] == str(mpirun.parent)
    assert str(tmp_path / "ompi" / "lib") not in env.get("LD_LIBRARY_PATH", ""), (
        "MPI lib dir must not be injected when no ../lib sibling exists"
    )


def test_orca_runtime_env_keeps_explicit_ld_library_path(tmp_path: Path) -> None:
    bin_dir = tmp_path / "ompi" / "bin"
    mpirun = _make_executable(bin_dir)
    (tmp_path / "ompi" / "lib").mkdir()

    env = orca_runtime_env("/custom/qc/libs", mpi_path=mpirun)

    assert env is not None
    ld_entries = env["LD_LIBRARY_PATH"].split(os.pathsep)
    assert ld_entries[0] == str(tmp_path / "ompi" / "lib")
    assert "/custom/qc/libs" in ld_entries


def test_orca_runtime_env_uses_sniffed_mpi(tmp_path: Path) -> None:
    sniffed = _make_executable(tmp_path / "ompi" / "bin")
    sniff_result = ShellEnvironment(mpirun=sniffed, source="login-shell")

    with (
        patch.object(software.shutil, "which", return_value=None),
        patch.object(software, "sniff_login_shell_env", return_value=sniff_result),
    ):
        env = orca_runtime_env(None)

    assert env is not None
    assert env["PATH"].split(os.pathsep)[0] == str(sniffed.parent)


def test_orca_runtime_env_no_mpi_and_no_ld_inherits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(software.MPI_ENV_VAR, "")
    with (
        patch.object(software.shutil, "which", return_value=None),
        patch.object(software, "sniff_rc_files", return_value=ShellEnvironment()),
        patch("glob.glob", return_value=[]),
    ):
        assert orca_runtime_env(None) is None


def test_sniff_disabled_by_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(software.SNIFF_DISABLE_ENV_VAR, "1")

    with patch("subprocess.run") as mock_run:
        assert sniff_login_shell_env() is None

    mock_run.assert_not_called()


def test_sniff_login_shell_env_caches_single_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(software.SNIFF_DISABLE_ENV_VAR, raising=False)
    stdout = f"/usr/bin{os.pathsep}/bin\x00\x00"
    fake = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    with patch("subprocess.run", return_value=fake) as mock_run:
        first = sniff_login_shell_env()
        second = sniff_login_shell_env()

    assert first is second
    assert first is not None
    assert mock_run.call_count == 1
    assert first.source == "login-shell"


def test_sniff_login_shell_env_negative_caches_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(software.SNIFF_DISABLE_ENV_VAR, raising=False)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="bash", timeout=5),
    ) as mock_run:
        first = sniff_login_shell_env()
        second = sniff_login_shell_env()

    assert first is None
    assert second is None
    assert mock_run.call_count == 1


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_sniff_login_shell_env_real_bash_returns_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(software.SNIFF_DISABLE_ENV_VAR, raising=False)
    software._reset_shell_env_cache()
    try:
        env = sniff_login_shell_env(timeout=10.0)
    finally:
        software._reset_shell_env_cache()

    assert env is not None
    assert env.source == "login-shell"
    assert len(env.path_dirs) > 0
