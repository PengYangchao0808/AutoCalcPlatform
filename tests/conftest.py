"""Shared test fixtures for ACP test suite."""

from __future__ import annotations

import os
import shutil

import pytest

_ALL_CONFSEARCH_ENV_VARS = [
    "CONFSEARCH_NPROC",
    "CONFSEARCH_MEM",
    "CONFSEARCH_ORCA_PATH",
    "CONFSEARCH_XTB_PATH",
    "CONFSEARCH_CREST_PATH",
    "CONFSEARCH_ISOSTAT_PATH",
    "CONFSEARCH_SHERMO_PATH",
    "CONFSEARCH_PROTOCOL",
]

_DEFAULT_EXECUTABLES = {
    "orca": "orca",
    "crest": "crest",
    "xtb": "xtb",
    "isostat": "isostat",
    "shermo": "Shermo",
}


def _resolve_executable_path(
    name: str, configured_path: str | os.PathLike[str] | None
) -> str:
    if configured_path:
        return os.fspath(configured_path)
    env_path = os.environ.get(f"CONFSEARCH_{name.upper()}_PATH")
    if env_path:
        return env_path
    return _DEFAULT_EXECUTABLES[name]


def _has_executable(name: str, configured_path: str | os.PathLike[str] | None = None) -> bool:
    return shutil.which(_resolve_executable_path(name, configured_path)) is not None


HAS_ORCA = _has_executable("orca")
HAS_CREST = _has_executable("crest")
HAS_XTB = _has_executable("xtb")
HAS_ISOSTAT = _has_executable("isostat")
HAS_SHERMO = _has_executable("shermo")

requires_orca = pytest.mark.skipif(not HAS_ORCA, reason="ORCA not available")
requires_crest = pytest.mark.skipif(not HAS_CREST, reason="CREST not available")
requires_xtb = pytest.mark.skipif(not HAS_XTB, reason="xTB not available")
requires_isostat = pytest.mark.skipif(not HAS_ISOSTAT, reason="ISOSTAT not available")
requires_shermo = pytest.mark.skipif(not HAS_SHERMO, reason="Shermo not available")


@pytest.fixture(autouse=True)
def _clean_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete all CONFSEARCH_* env vars before every test for isolation."""
    for var in _ALL_CONFSEARCH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture()
def sample_config() -> dict[str, object]:
    """Minimal valid configuration for backend tests."""
    return {
        "executables": {
            "orca": {"path": "orca"},
            "crest": {"path": "crest", "gfn_level": 2},
            "xtb": {"path": "xtb"},
            "isostat": {"path": "isostat"},
            "shermo": {"path": "Shermo"},
        },
        "resources": {"nproc": 1, "mem": "1GB"},
        "theory": {
            "optimization": {
                "engine": "orca",
                "method": "B3LYP",
                "basis": "def2-SVP",
                "dispersion": "GD3BJ",
            },
            "frequency": {"engine": "orca"},
            "single_point": {
                "method": "wB97X-D4",
                "basis": "def2-TZVPP",
            },
            "preoptimization": {"gfn_level": 2},
        },
    }


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow and integration tests that require external binaries",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Alias for --run-slow",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_slow = config.getoption("--run-slow") or config.getoption("--run-integration")
    skip_slow = pytest.mark.skip(reason="Pass --run-slow or --run-integration to run")
    for item in items:
        if any(item.get_closest_marker(mark) for mark in ("slow", "integration")):
            if not run_slow:
                item.add_marker(skip_slow)


def pytest_configure(config: pytest.Config) -> None:
    markers = [
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
        "integration: marks tests as integration tests (require external binaries)",
        "requires_orca: marks tests that need ORCA installed",
        "requires_crest: marks tests that need CREST installed",
        "requires_xtb: marks tests that need xTB installed",
        "requires_isostat: marks tests that need ISOSTAT installed",
        "requires_shermo: marks tests that need Shermo installed",
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)
