"""Round-7 #7c — the GNOME Orca screen reader must never resolve as ORCA.

``/usr/bin/orca`` on desktop Linux is the GNOME *screen reader*: a Python
program whose ``--version`` prints a bare ``40.3``.  Handing it to the QC layer
launches an accessibility application instead of a calculation.  Resolution
happens at the single boundary (``cccp.software``): the real ORCA keeps the
same lookup order, a rejected candidate is skipped (a later real install still
wins), and the typed refusal tells the operator where to pin the path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import cccp.software as software
from cccp.qc.interfaces.orca import ORCAInterface
from cccp.software import (
    SoftwareNotFoundError,
    SoftwareUnavailableError,
    discover_candidates,
    require_executable,
    resolve_executable,
    resolve_executable_with_source,
)

#: The GNOME screen reader's ``orca --version`` output on the reporting host.
_SCREEN_READER_OUTPUT = "40.3\n"
#: The ORCA QC driver's --version banner.
_QC_ORCA_OUTPUT = "Program Version 6.1.1  -  RELEASE   -\n"


def _script(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _screen_reader(directory: Path, name: str = "orca") -> Path:
    return _script(directory, name, f"printf '{_SCREEN_READER_OUTPUT}'")


def _real_orca(directory: Path, name: str = "orca") -> Path:
    return _script(directory, name, f"printf '{_QC_ORCA_OUTPUT}'")


@pytest.fixture(autouse=True)
def _isolate_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Resolve only inside this test's fake PATH (no system installs)."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.setattr(software, "_search_path", lambda: str(fake_bin))
    monkeypatch.setattr(software, "FALLBACKS", {})
    monkeypatch.setattr(software, "SCAN_PATTERNS", {})
    software._EXECUTABLE_PROBE_CACHE.clear()
    software._VERSION_CACHE.clear()
    return fake_bin


# ── probe classifier ────────────────────────────────────────────────────


def test_screen_reader_signatures_are_detected() -> None:
    assert software._orca_screen_reader_reason("40.3") is not None
    assert software._orca_screen_reader_reason("Orca Screen Reader 46.2") is not None
    assert software._orca_screen_reader_reason(
        "Traceback ... import pyatspi"
    ) is not None
    assert software._orca_screen_reader_reason("at-spi2 is required") is not None


def test_qc_orca_banners_are_not_rejected() -> None:
    assert software._orca_screen_reader_reason(_QC_ORCA_OUTPUT) is None
    assert software._orca_screen_reader_reason("Program Version 5.0.4") is None
    assert software._orca_screen_reader_reason("") is None
    assert software._orca_screen_reader_reason("ORCA 6.0.0") is None


# ── resolution boundary ─────────────────────────────────────────────────


def test_screen_reader_only_resolves_to_nothing(_isolate_resolution: Path) -> None:
    _screen_reader(_isolate_resolution)
    assert resolve_executable("orca") is None
    assert resolve_executable_with_source("orca") == (None, None)


def test_require_executable_reports_typed_backend_unavailable(
    _isolate_resolution: Path,
) -> None:
    decoy = _screen_reader(_isolate_resolution)
    with pytest.raises(SoftwareUnavailableError) as excinfo:
        require_executable("orca")
    error = excinfo.value
    assert isinstance(error, SoftwareNotFoundError)
    assert error.params["rule"] == "backend_unavailable"
    assert error.params["software"] == "orca"
    assert error.params["path"] == str(decoy.resolve())
    assert "screen reader" in error.params["reason"]
    message = str(error)
    assert "executables.orca.path" in message
    assert "CONFSEARCH_ORCA_PATH" in message


def test_real_orca_still_resolves(_isolate_resolution: Path) -> None:
    real = _real_orca(_isolate_resolution)
    assert resolve_executable("orca") == real.resolve()
    assert require_executable("orca") == real.resolve()


def test_lookup_order_skips_the_decoy_and_finds_the_real_install(
    _isolate_resolution: Path, tmp_path: Path
) -> None:
    # /usr/bin/orca (decoy) first, the real install later in PATH order.
    _screen_reader(_isolate_resolution)
    real_dir = tmp_path / "opt-orca" / "bin"
    real_dir.mkdir(parents=True)
    real = _real_orca(real_dir)
    software_monkeypatch = pytest.MonkeyPatch()
    try:
        software_monkeypatch.setattr(
            software,
            "_search_path",
            lambda: str(_isolate_resolution) + ":" + str(real_dir),
        )
        resolved, source = resolve_executable_with_source("orca")
    finally:
        software_monkeypatch.undo()
    assert resolved == real.resolve()
    assert source == "path"


def test_configured_decoy_falls_through_to_path(_isolate_resolution: Path) -> None:
    decoy = _screen_reader(_isolate_resolution.parent / "usr-bin")
    real = _real_orca(_isolate_resolution)
    assert resolve_executable("orca", configured_path=decoy) == real.resolve()


def test_configured_decoy_only_is_refused(_isolate_resolution: Path) -> None:
    decoy = _screen_reader(_isolate_resolution.parent / "usr-bin")
    assert resolve_executable("orca", configured_path=decoy) is None
    with pytest.raises(SoftwareUnavailableError):
        require_executable("orca", configured_path=decoy)


def test_discovery_does_not_offer_the_decoy(_isolate_resolution: Path) -> None:
    _screen_reader(_isolate_resolution)
    assert discover_candidates("orca") == []


def test_other_software_is_not_probed(_isolate_resolution: Path) -> None:
    # A bare version output is only suspicious for ``orca``.
    xtb = _script(_isolate_resolution, "xtb", "printf '40.3\\n'")
    assert resolve_executable("xtb") == xtb.resolve()


def test_ambiguous_probe_output_is_accepted(_isolate_resolution: Path) -> None:
    # A probe that fails / prints an unrecognised banner is "unknown", not a
    # rejection: an unusual but working ORCA build must keep resolving.
    weird = _script(_isolate_resolution, "orca", "echo 'some local build'; exit 1")
    assert resolve_executable("orca") == weird.resolve()


def test_probe_verdict_is_cached(_isolate_resolution: Path) -> None:
    _screen_reader(_isolate_resolution)
    calls = {"n": 0}
    real_run = subprocess.run

    def counting_run(*args, **kwargs):
        calls["n"] += 1
        return real_run(*args, **kwargs)

    with patch.object(software.subprocess, "run", side_effect=counting_run):
        assert resolve_executable("orca") is None
        assert resolve_executable("orca") is None
    assert calls["n"] == 1


# ── execution boundary ─────────────────────────────────────────────────


def test_orca_interface_refuses_to_run_the_screen_reader(
    _isolate_resolution: Path,
) -> None:
    decoy = _screen_reader(_isolate_resolution.parent / "usr-bin")
    interface = ORCAInterface(
        {"executables": {"orca": {"path": str(decoy)}}, "resources": {"nproc": 1, "mem": "1GB"}}
    )
    assert interface.executable is None
    with pytest.raises(SoftwareUnavailableError) as excinfo:
        interface._require_executable()  # noqa: SLF001 - the execution guard
    assert excinfo.value.params["rule"] == "backend_unavailable"
    assert excinfo.value.params["path"] == str(decoy.resolve())


def test_interface_never_reresolves_a_runnable_binary_when_marked_missing(
    _isolate_resolution: Path,
) -> None:
    """A ``None`` executable stays authoritative — no silent re-resolution.

    Re-resolving here would let a PATH change swap in a different binary and
    *execute* it, bypassing the state the caller (or a test) deliberately set.
    """
    _real_orca(_isolate_resolution)
    interface = ORCAInterface.__new__(ORCAInterface)
    interface.executable = None
    interface.exe_path = Path("orca")

    with pytest.raises(SoftwareNotFoundError, match="ORCA executable not found"):
        interface._require_executable()  # noqa: SLF001 - the execution guard
