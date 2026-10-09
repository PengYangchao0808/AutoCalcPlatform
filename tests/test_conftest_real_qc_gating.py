"""T01/D8 — real-QC gating resolves through the production config chain.

Pure-Python coverage of ``tests.conftest.resolve_real_qc_snapshot``: no QC
binary is executed and no version probe is spawned.  Every case controls
``HOME`` / ``PATH`` / cwd and calls the resolver directly, so assertions are
deterministic regardless of the host's real software installs.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import pytest

import cccp.software as software
from tests import conftest
from tests.conftest import (
    NOT_VERIFIED,
    RealQCSnapshot,
    requires_orca,
    resolve_real_qc_snapshot,
)


def _make_executable(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _controlled_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate HOME/cwd/PATH so only explicitly written config can resolve."""
    home = tmp_path / "home"
    home.mkdir()
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("PATH", "")
    monkeypatch.chdir(workdir)
    return home


@pytest.fixture(autouse=True)
def _isolate_ambient_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip real-world fallback/scan roots (same idiom as test_cccp_software).

    Production priority logic is untouched; only the ambient machine state is
    removed so a host install cannot leak into a controlled assertion.
    """
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(software, "SCAN_PATTERNS", {})
    monkeypatch.setattr(software, "_search_path", lambda: os.environ.get("PATH", ""))


def test_configured_absolute_path_opens_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(a) user YAML absolute paths open the gate with the configured path."""
    home = _controlled_env(monkeypatch, tmp_path)
    orca = _make_executable(tmp_path / "bin", "orca")
    crest = _make_executable(tmp_path / "bin", "crest")
    (home / ".cccp.yaml").write_text(
        f"executables:\n  orca:\n    path: {orca}\n  crest:\n    path: {crest}\n",
        encoding="utf-8",
    )

    snapshot = resolve_real_qc_snapshot()

    assert snapshot.available("orca") is True
    assert snapshot.path("orca") == orca.resolve()
    assert snapshot.binary("orca").source == "config"
    assert snapshot.available("crest") is True
    assert snapshot.path("crest") == crest.resolve()


@pytest.mark.parametrize("name", ["orca", "crest", "xtb", "isostat", "shermo", "censo"])
def test_no_source_closed_with_not_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """(b) no PATH and no config -> closed gate with NOT_VERIFIED provenance."""
    _controlled_env(monkeypatch, tmp_path)

    snapshot = resolve_real_qc_snapshot()

    binary = snapshot.binary(name)
    assert binary.available is False
    assert binary.path is None
    reason = snapshot.skip_reason(name)
    assert reason.startswith(NOT_VERIFIED)
    assert "source=none" in reason
    assert "sources_consulted" in reason
    assert f"CONFSEARCH_{name.upper()}_PATH" in reason


def test_env_overrides_explicit_yaml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """(c) CONFSEARCH_* env wins over an explicit --config YAML file."""
    _controlled_env(monkeypatch, tmp_path)
    yaml_orca = _make_executable(tmp_path / "yaml_bin", "orca")
    env_orca = _make_executable(tmp_path / "env_bin", "orca")
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text(
        f"executables:\n  orca:\n    path: {yaml_orca}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFSEARCH_ORCA_PATH", str(env_orca))

    snapshot = resolve_real_qc_snapshot(config_path=explicit)

    assert snapshot.path("orca") == env_orca.resolve()
    assert snapshot.path("orca") != yaml_orca.resolve()


def test_mock_launch_hook_path_equals_gate_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(d) the launch path is verbatim the snapshot path used by the gate."""
    home = _controlled_env(monkeypatch, tmp_path)
    orca = _make_executable(tmp_path / "bin", "orca")
    (home / ".cccp.yaml").write_text(
        f"executables:\n  orca:\n    path: {orca}\n",
        encoding="utf-8",
    )
    snapshot = resolve_real_qc_snapshot()

    launched: list[str] = []

    def mock_launch(binary_path: Path | None) -> None:
        assert binary_path is not None
        launched.append(str(binary_path))

    gate_path = snapshot.path("orca") if snapshot.available("orca") else None
    mock_launch(gate_path)

    assert launched == [str(orca)]
    assert launched[0] == str(snapshot.path("orca"))


@requires_orca
def test_real_marker_launch_path_matches_snapshot(
    real_qc_binary_path: Callable[[str], Path | None],
    real_qc_snapshot: RealQCSnapshot,
) -> None:
    """(d) a real-marker case launches the session snapshot's exact path."""
    gate_path = real_qc_snapshot.path("orca")
    assert gate_path is not None
    assert real_qc_binary_path("orca") == gate_path


def test_module_snapshot_is_not_re_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    """stale_state: env cleanup must not mutate the collection-time decision."""
    before = conftest.get_real_qc_snapshot()
    monkeypatch.setenv("CONFSEARCH_ORCA_PATH", "/definitely/missing/orca")

    after = conftest.get_real_qc_snapshot()

    assert after is before
    assert after.path("orca") == before.path("orca")
    assert after.skip_reason("orca") == before.skip_reason("orca")
    assert conftest.HAS_ORCA is before.available("orca")
