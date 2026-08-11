"""Tests for the centralized QC executable resolver (cccp.software)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from cccp.software import detect_version


def _completed(retcode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], retcode, stdout=stdout, stderr=stderr)


def test_detect_version_returns_none_without_executable() -> None:
    assert detect_version("censo", None) is None


def test_detect_version_no_probe_for_unsupported_software() -> None:
    with patch("subprocess.run") as mock_run:
        assert detect_version("orca", Path("/usr/bin/orca")) is None
        mock_run.assert_not_called()


def test_censo_falls_back_from_invalid_to_valid_flag() -> None:
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "-v":
            return _completed(2, stderr="censo: error: ambiguous option: -v")
        return _completed(0, stdout="1.2.0")

    with patch("subprocess.run", side_effect=_fake_run):
        version = detect_version("censo", Path("/opt/software/censo/censo"))

    assert version == "1.2.0"
    assert calls == [
        ["/opt/software/censo/censo", "-v"],
        ["/opt/software/censo/censo", "-version"],
    ]


def test_detect_version_ignores_nonzero_exit_even_with_output() -> None:
    with patch("subprocess.run", return_value=_completed(1, stdout="some banner")):
        assert detect_version("xtb", Path("/usr/bin/xtb")) is None


def test_detect_version_takes_first_line_of_successful_probe() -> None:
    with patch(
        "subprocess.run",
        return_value=_completed(0, stdout="xtb version 6.7.1\nmore text"),
    ):
        assert detect_version("xtb", Path("/usr/bin/xtb")) == "xtb version 6.7.1"
