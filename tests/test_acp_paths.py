"""Tests for acp.core.paths — run-root resolution and safety checks."""

from __future__ import annotations

from pathlib import Path

import pytest

from acp.core.paths import (
    ALLOW_SLOW_FS_ENV_VAR,
    RUN_ROOT_ENV_VAR,
    check_run_root_safety,
    mount_fstype_for,
    platform_default_run_root,
    resolve_run_root,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(RUN_ROOT_ENV_VAR, raising=False)
    monkeypatch.delenv(ALLOW_SLOW_FS_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)


class TestResolveRunRoot:
    def test_explicit_argument_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RUN_ROOT_ENV_VAR, str(tmp_path / "env-root"))
        assert resolve_run_root(tmp_path / "cli-root") == (tmp_path / "cli-root").resolve()

    def test_env_fallback(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv(RUN_ROOT_ENV_VAR, str(tmp_path / "env-root"))
        assert resolve_run_root() == (tmp_path / "env-root").resolve()

    def test_platform_default_without_cli_or_env(self) -> None:
        resolved = resolve_run_root()
        assert resolved.is_absolute()
        assert "ACP_runs" not in str(resolved)

    def test_tilde_expansion(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RUN_ROOT_ENV_VAR, "~/acp-runs-test")
        resolved = resolve_run_root()
        assert resolved.is_absolute()
        assert not str(resolved).startswith("~")

    def test_relative_path_resolved_against_cwd(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.chdir(tmp_path)
        assert resolve_run_root("rel-root") == (tmp_path / "rel-root").resolve()


class TestPlatformDefault:
    def test_user_default_uses_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("acp.core.paths.os.geteuid", lambda: 1000)
        monkeypatch.setenv("XDG_DATA_HOME", "/xdg-test")
        assert platform_default_run_root() == Path("/xdg-test/acp/runs")

    def test_default_never_points_into_cwd(self) -> None:
        default = platform_default_run_root()
        assert default.is_absolute()
        assert not str(default).startswith("./")


class TestMountFstype:
    def test_root_mount_resolves(self) -> None:
        assert mount_fstype_for(Path("/")) is not None

    def test_none_when_proc_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            Path,
            "read_text",
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError("no /proc")),
        )
        assert mount_fstype_for(Path("/tmp")) is None


class TestCheckRunRootSafety:
    def test_clean_native_dir(self, tmp_path: Path) -> None:
        assert check_run_root_safety(tmp_path) == []

    def test_slow_filesystem_warning(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("acp.core.paths.mount_fstype_for", lambda _path: "9p")
        warnings = check_run_root_safety(tmp_path)
        assert any("slow filesystem" in warning and "9p" in warning for warning in warnings)

    def test_allow_slow_fs_silences_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("acp.core.paths.mount_fstype_for", lambda _path: "9p")
        monkeypatch.setenv(ALLOW_SLOW_FS_ENV_VAR, "1")
        assert check_run_root_safety(tmp_path) == []

    def test_install_tree_overlap_warning(self) -> None:
        import acp.core.paths as paths_module

        install_root = Path(paths_module.__file__).resolve().parents[3]
        fake_root = install_root / "somewhere" / "data"
        warnings = check_run_root_safety(fake_root)
        assert any("install tree" in warning for warning in warnings)
