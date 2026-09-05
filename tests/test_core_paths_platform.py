"""Platform-specific tests for acp.core.paths — Windows and POSIX branches."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from acp.core.paths import (
    RUN_ROOT_ENV_VAR,
    platform_default_run_root,
    resolve_run_root,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RUN_ROOT_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)


class TestWindowsPlatform:
    """Simulate Windows: os.geteuid missing, LOCALAPPDATA / os.name fallback."""

    def test_no_geteuid_does_not_crash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delattr(os, "geteuid")
        result = platform_default_run_root()
        assert result.is_absolute()

    def test_no_geteuid_with_localappdata(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delattr(os, "geteuid")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
        result = platform_default_run_root()
        assert result == tmp_path / "AppData" / "Local" / "acp" / "runs"

    def test_no_geteuid_without_localappdata_nt(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delattr(os, "geteuid")
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(Path, "home", classmethod(lambda _cls: tmp_path / "home"))
        result = platform_default_run_root()
        assert result == tmp_path / "home" / "AppData" / "Local" / "acp" / "runs"

    def test_resolve_run_root_skips_windows_branch_with_explicit(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delattr(os, "geteuid")
        explicit = tmp_path / "explicit"
        result = resolve_run_root(explicit)
        assert result == explicit.resolve()


class TestPosixPlatform:
    """Existing POSIX behavior: geteuid available."""

    def test_root_branch_when_geteuid_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("acp.core.paths.os.geteuid", lambda: 0)
        result = platform_default_run_root()
        assert result.is_absolute()
        assert "ACP_runs" not in str(result)

    def test_user_branch_uses_xdg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("acp.core.paths.os.geteuid", lambda: 1000)
        monkeypatch.setenv("XDG_DATA_HOME", "/xdg-test")
        assert platform_default_run_root() == Path("/xdg-test/acp/runs")

    def test_default_never_points_into_cwd(self) -> None:
        default = platform_default_run_root()
        assert default.is_absolute()
        assert not str(default).startswith("./")
