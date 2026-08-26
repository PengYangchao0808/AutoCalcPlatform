# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false
"""Shared test fixtures for ACP test suite."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from acp.backends.base import QCResult
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult

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


def _resolve_executable_path(name: str, configured_path: str | os.PathLike[str] | None) -> str:
    if configured_path:
        return os.fspath(configured_path)
    env_path = os.environ.get(f"CONFSEARCH_{name.upper()}_PATH")
    if env_path:
        return env_path
    return _DEFAULT_EXECUTABLES[name]


def _has_executable(name: str, configured_path: str | os.PathLike[str] | None = None) -> bool:
    return shutil.which(_resolve_executable_path(name, configured_path)) is not None


@dataclass(frozen=True, slots=True)
class FakeBackendCall:
    """One invocation recorded by :class:`FakeBackend`."""

    backend: str
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


class FakeBackend:
    """In-memory capability backend shared by calculation tests.

    The response queue is deliberately mutable: tests can describe a first
    failure followed by a successful retry without mocking individual methods.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[FakeBackendCall] = []
        self.backend_requests: list[str] = []
        self._backend_name = self.name
        self._responses: dict[str, list[QCResult | RelaxedScanResult | Exception]] = {}

    def set_backend_name(self, backend_name: str) -> None:
        """Set the backend name attached to subsequent recorded calls."""
        self._backend_name = backend_name

    def set_result(
        self,
        method: str,
        result: QCResult | RelaxedScanResult | None = None,
        **fields: Any,
    ) -> None:
        """Queue one result for *method*, optionally constructing ``QCResult``."""
        value = result if result is not None else QCResult(**fields)
        self._responses[method] = [value]

    def set_results(
        self, method: str, results: list[QCResult | RelaxedScanResult | Exception]
    ) -> None:
        """Queue an ordered sequence of results or exceptions for *method*."""
        self._responses[method] = list(results)

    def fail_next(self, method: str, error: Exception | None = None) -> None:
        """Queue one raised error before any already-configured responses."""
        failure = error or RuntimeError(f"fake {method} failure")
        self._responses.setdefault(method, []).insert(0, failure)

    def is_available(self) -> bool:
        """Report availability for code paths that probe a backend."""
        return True

    def _respond(self, operation_name: str, *args: Any, **kwargs: Any) -> QCResult:
        self.calls.append(
            FakeBackendCall(
                backend=self._backend_name,
                method=operation_name,
                args=args,
                kwargs=dict(kwargs),
            )
        )
        queue = self._responses.get(operation_name)
        response = queue.pop(0) if queue else None
        if isinstance(response, Exception):
            raise response
        if isinstance(response, QCResult):
            return response
        if response is not None:
            raise TypeError(f"{operation_name} requires a QCResult response")

        coordinates = np.asarray(args[0], dtype=float).copy()
        symbols = list(args[1])
        output_dir = kwargs.get("output_dir")
        output_path = Path(output_dir) if isinstance(output_dir, (str, Path)) else None
        return QCResult(
            success=True,
            energy=-1.0,
            coordinates=coordinates,
            symbols=symbols,
            converged=True,
            output_file=output_path / f"{operation_name}.out" if output_path else None,
            log_file=output_path / f"{operation_name}.log" if output_path else None,
            frequencies=[100.0, 200.0] if operation_name == "frequency" else None,
            has_frequencies=operation_name == "frequency",
        )

    def optimize(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer an unconstrained optimization call."""
        return self._respond("optimize", *args, **kwargs)

    def single_point(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer a single-point call."""
        return self._respond("single_point", *args, **kwargs)

    def frequency(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer a frequency call."""
        return self._respond("frequency", *args, **kwargs)

    def transition_state_opt(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer a transition-state optimization call."""
        return self._respond("transition_state_opt", *args, **kwargs)

    def relaxed_scan(self, *args: Any, **kwargs: Any) -> RelaxedScanResult:
        """Record and answer a relaxed-scan call for calculation primitive tests."""
        self.calls.append(
            FakeBackendCall(
                backend=self._backend_name,
                method="relaxed_scan",
                args=args,
                kwargs=dict(kwargs),
            )
        )
        queue = self._responses.get("relaxed_scan")
        response = queue.pop(0) if queue else None
        if isinstance(response, Exception):
            raise response
        if isinstance(response, RelaxedScanResult):
            return response

        coordinates = np.asarray(args[0], dtype=float).copy()
        symbols = list(args[1])
        output_dir = Path(kwargs["output_dir"])
        plan = kwargs["plan"]
        points = [
            RelaxedScanPoint(
                frame_index=index,
                progress=index / (plan.points - 1),
                coordinates=coordinates.copy(),
                symbols=symbols.copy(),
                energy_hartree=-1.0 - index * 0.01,
                success=True,
                coordinate_values=plan.coordinate_targets(index),
            )
            for index in range(plan.points)
        ]
        return RelaxedScanResult(
            points=points,
            input_xyz=output_dir / "input.xyz",
            scan_dir=output_dir,
            success=True,
        )

    def scan(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer a scan call for later primitive tests."""
        return self._respond("scan", *args, **kwargs)

    def irc(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer an IRC call for later primitive tests."""
        return self._respond("irc", *args, **kwargs)


@pytest.fixture()
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Patch ``acp.backends.get_backend`` with a fresh recording fake."""
    backend = FakeBackend()

    def get_backend(name: str) -> FakeBackend:
        backend.backend_requests.append(name)
        backend.set_backend_name(name)
        return backend

    monkeypatch.setattr("acp.backends.get_backend", get_backend)
    return backend


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
    # ACP_RUN_ROOT is set by several API/scheduler tests via bare os.environ
    # (leaked into later tests, breaking handoff jobs_root() resolution).
    # Test-local overrides re-set it inside the test body.
    monkeypatch.delenv("ACP_RUN_ROOT", raising=False)


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
