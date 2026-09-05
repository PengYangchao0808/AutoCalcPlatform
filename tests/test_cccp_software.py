"""Tests for the centralized QC executable resolver (cccp.software)."""

# pyright: reportAny=false, reportExplicitAny=false, reportUnknownMemberType=false

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

import cccp.software as software
from cccp.software import (
    SoftwareCandidate,
    detect_version,
    discover_all_detailed,
    discover_candidates,
    normalize_version,
    resolve_executable,
    resolve_executable_with_source,
    version_cached,
)


def _completed(
    retcode: int,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], retcode, stdout=stdout, stderr=stderr)


def _make_executable(directory: Path, name: str = "orca") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture(autouse=True)
def _clear_version_cache() -> None:
    software._VERSION_CACHE.clear()


@pytest.fixture(autouse=True)
def _isolate_scan_patterns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep real-world scan roots out of unit tests unless opted in."""
    monkeypatch.setattr(software, "SCAN_PATTERNS", {})


def test_detect_version_returns_none_without_executable() -> None:
    assert detect_version("censo", None) is None


def test_detect_version_no_probe_for_unsupported_software() -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _completed(0, stdout="Program Version 6.1.1\n")

        assert detect_version("orca", Path("/usr/bin/orca")) == "Program Version 6.1.1"
        mock_run.assert_called_once_with(
            ["/usr/bin/orca", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=mock_run.call_args.kwargs["env"],
        )


def test_censo_falls_back_from_invalid_to_valid_flag() -> None:
    calls = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        _ = kwargs
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


def test_detect_version_skips_decorative_banner_line() -> None:
    """xtb --version prints an ASCII banner whose first line is a dashed
    separator; the version must come from the semver token instead."""
    banner = (
        "      -----------------------------------------------------------\n"
        "     |                   =====================                   |\n"
        "     |                           x T B                           |\n"
        "      -----------------------------------------------------------\n"
        "       xtb version 6.6.1 (abcdef0) compiled by 'runner'\n"
    )
    with patch("subprocess.run", return_value=_completed(0, stdout=banner)):
        assert detect_version("xtb", Path("/usr/bin/xtb")) == "6.6.1"


# --- resolution with source -------------------------------------------------


def test_resolve_executable_with_source_config(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    path, source = resolve_executable_with_source("orca", configured_path=binary)
    assert path == binary.resolve()
    assert source == "config"
    # resolve_executable keeps its original return type/behavior
    assert resolve_executable("orca", configured_path=binary) == binary.resolve()


def test_resolve_executable_with_source_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path)
    monkeypatch.setenv("CONFSEARCH_ORCA_PATH", str(binary))
    path, source = resolve_executable_with_source("orca")
    assert path == binary.resolve()
    assert source == "env"


def test_resolve_executable_with_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_executable(tmp_path / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    path, source = resolve_executable_with_source("orca")
    assert path == binary.resolve()
    assert source == "path"


def test_resolve_executable_with_source_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(software, "FALLBACKS", {"orca": ["/opt/orca/orca"]})
    with patch.object(software, "_valid_executable", side_effect=lambda p: Path(p) if p else None):
        path, source = resolve_executable_with_source("orca")
    assert path == Path("/opt/orca/orca")
    assert source == "fallback"


def test_resolve_executable_with_source_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(Path("/nonexistent")))
    monkeypatch.setattr(software, "FALLBACKS", {})
    assert resolve_executable_with_source("orca") == (None, None)
    assert resolve_executable("orca") is None


# --- candidate enumeration --------------------------------------------------


def test_discover_candidates_enumerates_all_path_hits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = _make_executable(tmp_path / "v5")
    second = _make_executable(tmp_path / "v6")
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path / "v5"), str(tmp_path / "v6")]))
    monkeypatch.setattr(software, "FALLBACKS", {})

    candidates = discover_candidates("orca")

    assert [(c.path, c.source) for c in candidates] == [
        (first.resolve(), "path"),
        (second.resolve(), "path"),
    ]


def test_discover_candidates_priority_order_and_dedup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = _make_executable(tmp_path / "cfg")
    env_binary = _make_executable(tmp_path / "envdir")
    path_binary = _make_executable(tmp_path / "pathdir")
    # Same file reachable from PATH and as a symlink -> dedup by resolved path
    alias = tmp_path / "aliasdir"
    alias.mkdir()
    (alias / "orca").symlink_to(path_binary)

    monkeypatch.setenv("CONFSEARCH_ORCA_PATH", str(env_binary))
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join([str(tmp_path / "pathdir"), str(alias)]),
    )
    monkeypatch.setattr(software, "FALLBACKS", {})

    candidates = discover_candidates("orca", configured_path=configured)

    assert [(c.path, c.source) for c in candidates] == [
        (configured.resolve(), "config"),
        (env_binary.resolve(), "env"),
        (path_binary.resolve(), "path"),
    ]


def test_discover_candidates_scan_globs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scanned = _make_executable(tmp_path / "opt" / "orca_6_1_1")
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(
        software,
        "SCAN_PATTERNS",
        {"orca": (str(tmp_path / "opt" / "orca*" / "orca"), str(tmp_path / "missing*" / "orca"))},
    )

    candidates = discover_candidates("orca")

    assert [(c.path, c.source) for c in candidates] == [(scanned.resolve(), "scan")]


def test_discover_candidates_scan_after_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path_binary = _make_executable(tmp_path / "bin")
    scanned = _make_executable(tmp_path / "opt" / "orca_5_0_4")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(
        software, "SCAN_PATTERNS", {"orca": (str(tmp_path / "opt" / "orca*" / "orca"),)}
    )

    candidates = discover_candidates("orca")

    assert [(c.path, c.source) for c in candidates] == [
        (path_binary.resolve(), "path"),
        (scanned.resolve(), "scan"),
    ]


def test_discover_candidates_no_scan_patterns_for_other_software(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(
        software, "SCAN_PATTERNS", {"orca": (str(tmp_path / "opt" / "orca*" / "orca"),)}
    )
    assert discover_candidates("crest") == []


def test_discover_all_detailed_combines_resolution_and_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path_binary = _make_executable(tmp_path / "bin")
    _make_executable(tmp_path / "opt" / "orca_6_1_1")
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(
        software, "SCAN_PATTERNS", {"orca": (str(tmp_path / "opt" / "orca*" / "orca"),)}
    )

    detailed = discover_all_detailed(config={})

    assert set(detailed) == set(software.EXECUTABLES)
    orca = detailed["orca"]
    assert orca.resolved == path_binary.resolve()
    assert orca.source == "path"
    assert [c.source for c in orca.candidates] == ["path", "scan"]
    assert len(orca.candidates) > 1
    assert detailed["crest"].resolved is None
    assert detailed["crest"].source is None
    assert detailed["crest"].candidates == ()


# --- version normalization + TTL cache ---------------------------------------


def test_normalize_version_extracts_semver_token() -> None:
    assert normalize_version("Program Version 6.1.1") == "6.1.1"
    assert normalize_version("xtb version 6.7.1 (commit abc)") == "6.7.1"
    assert normalize_version("  5.0.4\n") == "5.0.4"


def test_normalize_version_fallback_truncates_raw() -> None:
    assert normalize_version("unknown build") == "unknown build"
    assert normalize_version("x" * 100) == "x" * 64


def test_normalize_version_empty() -> None:
    assert normalize_version(None) == ""
    assert normalize_version("") == ""


def test_version_cached_probes_once_per_ttl(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    with patch.object(software, "detect_version", return_value="Program Version 6.1.1") as probe:
        assert version_cached("orca", binary) == "6.1.1"
        assert version_cached("orca", binary) == "6.1.1"
        assert probe.call_count == 1


def test_version_cached_negative_caching(tmp_path: Path) -> None:
    binary = _make_executable(tmp_path)
    with patch.object(software, "detect_version", return_value=None) as probe:
        assert version_cached("orca", binary) == ""
        assert version_cached("orca", binary) == ""
        assert probe.call_count == 1


def test_version_cached_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = _make_executable(tmp_path)
    with patch.object(software, "detect_version", return_value="1.2.3") as probe:
        assert version_cached("orca", binary) == "1.2.3"
        monkeypatch.setattr(software, "VERSION_CACHE_TTL", -1.0)
        assert version_cached("orca", binary) == "1.2.3"
        assert probe.call_count == 2


def test_version_cached_none_executable() -> None:
    assert version_cached("orca", None) == ""


def test_software_candidate_is_frozen(tmp_path: Path) -> None:
    candidate = SoftwareCandidate(path=tmp_path, source="path")
    with pytest.raises(AttributeError):
        candidate.source = "env"  # type: ignore[misc]


def test_run_shermo_resolves_absolute_config_path(tmp_path: Path) -> None:
    """run_shermo goes through resolve_executable, so an absolute
    executables.shermo.path works even when 'Shermo' is not on PATH."""
    binary = _make_executable(tmp_path)
    from cccp.qc.runners import run_shermo

    freq_log = tmp_path / "freq.log"
    freq_log.write_text("dummy", encoding="utf-8")
    with patch(
        "cccp.qc.runners.subprocess.run",
        return_value=subprocess.CompletedProcess(
            args=[str(binary)],
            returncode=0,
            stdout="Sum of electronic energy and thermal correction to G: -100.5\n",
            stderr="",
        ),
    ) as mock_run:
        result = run_shermo(
            freq_log,
            -100.0,
            tmp_path,
            shermo_bin=str(binary),
        )
    assert result is not None
    assert mock_run.call_args[0][0][0] == str(binary.resolve())


def test_run_shermo_returns_none_when_unresolvable(tmp_path: Path) -> None:
    """A missing Shermo yields None (contract) instead of a subprocess crash."""
    from cccp.qc.runners import run_shermo

    freq_log = tmp_path / "freq.log"
    freq_log.write_text("dummy", encoding="utf-8")
    with patch.object(software, "resolve_executable", return_value=None):
        result = run_shermo(freq_log, -100.0, tmp_path, shermo_bin="Shermo")
    assert result is None
