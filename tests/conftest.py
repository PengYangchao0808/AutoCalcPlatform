# pyright: reportAny=false, reportExplicitAny=false, reportUnknownVariableType=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnannotatedClassAttribute=false, reportUnusedFunction=false
"""Shared test fixtures for ACP test suite."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from acp.backends.base import QCResult
from cccp.config import load_config
from cccp.qc.interfaces.xtb_scan import RelaxedScanPoint, RelaxedScanResult
from cccp.software import (
    ENV_VARS as SOFTWARE_ENV_VARS,
)
from cccp.software import (
    discover_candidates,
    get_configured_path,
    resolve_executable_with_source,
)

_ALL_CONFSEARCH_ENV_VARS = [
    "CONFSEARCH_NPROC",
    "CONFSEARCH_MEM",
    "CONFSEARCH_ORCA_PATH",
    "CONFSEARCH_XTB_PATH",
    "CONFSEARCH_CREST_PATH",
    "CONFSEARCH_ISOSTAT_PATH",
    "CONFSEARCH_SHERMO_PATH",
    "CONFSEARCH_CENSO_PATH",
    "CONFSEARCH_PROTOCOL",
]

# --- three-state real-QC evidence rule (plan todo 49) ------------------------
# Real-QC tests have exactly three outcomes: PASS, FAIL, NOT_VERIFIED.  A
# skipped real-QC test is NOT_VERIFIED and must never be counted as a green
# pass.  Every binary-gate skip reason carries the literal token so
# ``pytest -rs`` output and evidence logs can enforce that rule mechanically.
NOT_VERIFIED = "NOT_VERIFIED"

_REAL_QC_DISPLAY_NAMES = {
    "orca": "ORCA",
    "crest": "CREST",
    "xtb": "xTB",
    "isostat": "ISOSTAT",
    "shermo": "Shermo",
    "censo": "CENSO",
}

#: Executable keys gated by the real-QC markers.
_REAL_QC_NAMES = ("orca", "crest", "xtb", "isostat", "shermo", "censo")


@dataclass(frozen=True, slots=True)
class ResolvedQCBinary:
    """One real-QC binary resolved through the *production* config chain.

    Attributes:
        name: Executable key (``orca``/``crest``/...).
        path: Resolved absolute path, or ``None`` when the gate is closed.
        source: Winning discovery source — ``config``/``env``/``path``/
            ``fallback``/``scan`` — or ``None`` when nothing resolved.
        provenance: Human-readable record of the sources consulted, included
            verbatim in NOT_VERIFIED skip reasons.
    """

    name: str
    path: Path | None
    source: str | None
    provenance: str

    @property
    def available(self) -> bool:
        return self.path is not None


@dataclass(frozen=True, slots=True)
class RealQCSnapshot:
    """Collection-time resolution snapshot shared by every gate consumer.

    Built ONCE (module import / session fixture) from
    :func:`cccp.config.load_config` + :func:`cccp.software.resolve_executable`,
    so the marker decision (``HAS_*``/``requires_*``), any test-body launch
    path and path assertions all read the *same* absolute paths.  Re-resolving
    inside a test body would diverge from the collection-time decision because
    the autouse :func:`_clean_env_vars` fixture deletes ``CONFSEARCH_*`` before
    every test.

    Resolution order (mirrors production, environment OVERRIDES explicit YAML):
    built-in defaults < ``~/.cccp.yaml`` < ``./cccp.yaml`` < ``--config`` file
    < ``CONFSEARCH_*`` env vars < CLI overrides.
    """

    binaries: dict[str, ResolvedQCBinary]
    config: dict[str, Any]
    config_path: str | None = None
    config_error: str | None = None

    def binary(self, name: str) -> ResolvedQCBinary:
        return self.binaries[name]

    def available(self, name: str) -> bool:
        return self.binaries[name].available

    def path(self, name: str) -> Path | None:
        return self.binaries[name].path

    def skip_reason(self, name: str) -> str:
        binary = self.binaries[name]
        display = _REAL_QC_DISPLAY_NAMES.get(name, name)
        detail = binary.provenance
        if self.config_error:
            detail = f"{detail}; config_error={self.config_error}"
        return (
            f"{NOT_VERIFIED}: {display} not available for real-QC execution "
            f"({detail}); skipped, never counted as a pass"
        )


def _resolution_provenance(
    name: str,
    configured_path: str | Path | None,
    resolved: Path | None,
    source: str | None,
) -> str:
    env_var = SOFTWARE_ENV_VARS.get(name, f"CONFSEARCH_{name.upper()}_PATH")
    env_state = "set" if os.environ.get(env_var) else "unset"
    configured = str(configured_path) if configured_path else None
    if resolved is not None:
        return f"source={source}; configured={configured!r}; {env_var}={env_state}"
    consulted = sorted(
        {
            candidate.source
            for candidate in discover_candidates(name, configured_path=configured_path)
        }
    )
    return (
        f"source=none; configured={configured!r}; {env_var}={env_state}; "
        f"sources_consulted={','.join(consulted) or 'none'}; no executable"
    )


def resolve_real_qc_snapshot(config_path: Path | None = None) -> RealQCSnapshot:
    """Resolve every real-QC binary via the production resolver, once.

    Uses :func:`cccp.config.load_config` (6-source merge, env overriding
    explicit YAML) followed by :func:`cccp.software.resolve_executable` — the
    single production resolver — rather than a home-grown ``PATH`` lookup.

    Args:
        config_path: Optional explicit ``--config`` file, merged before the
            ``CONFSEARCH_*`` environment overrides (production priority).

    Returns:
        A :class:`RealQCSnapshot` with per-binary absolute paths and provenance.
    """
    config_error: str | None = None
    try:
        config = load_config(config_path=Path(config_path) if config_path else None)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        # A malformed user config must not abort collection; the PATH/env
        # chain still applies and the error is surfaced in skip provenance.
        config = {}
        config_error = f"{type(exc).__name__}: {exc}"

    binaries: dict[str, ResolvedQCBinary] = {}
    for name in _REAL_QC_NAMES:
        configured_path = get_configured_path(config, name)
        resolved, source = resolve_executable_with_source(name, configured_path)
        provenance = _resolution_provenance(name, configured_path, resolved, source)
        binaries[name] = ResolvedQCBinary(
            name=name,
            path=resolved,
            source=source,
            provenance=provenance,
        )
    return RealQCSnapshot(
        binaries=binaries,
        config=config,
        config_path=str(config_path) if config_path else None,
        config_error=config_error,
    )


_REAL_QC_SNAPSHOT: RealQCSnapshot | None = None


def get_real_qc_snapshot() -> RealQCSnapshot:
    """Return the process-wide snapshot, resolving at most once per process."""
    global _REAL_QC_SNAPSHOT
    if _REAL_QC_SNAPSHOT is None:
        _REAL_QC_SNAPSHOT = resolve_real_qc_snapshot()
    return _REAL_QC_SNAPSHOT


# Resolve exactly once at conftest import (collection start).  Environment
# variables present here are the production-faithful env override; later
# ``_clean_env_vars`` deletions MUST NOT trigger a second resolution.
_REAL_QC_SNAPSHOT = resolve_real_qc_snapshot()


def real_qc_skip_reason(name: str) -> str:
    """Skip reason for a real-QC test whose binary is unavailable.

    ``name`` is an executable key (``orca``/``crest``/``xtb``/``isostat``/
    ``shermo``/``censo``).  Resolution follows production: a binary configured
    through ``~/.cccp.yaml`` / ``./cccp.yaml`` / ``--config`` (absolute path) or
    exported via ``CONFSEARCH_<NAME>_PATH`` now OPENS the gate — the reason
    embeds the resolution provenance so an operator can see every source
    consulted.
    """
    return get_real_qc_snapshot().skip_reason(name)


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

    def casscf(self, *args: Any, **kwargs: Any) -> QCResult:
        """Record and answer a CASSCF call for electronic-state tests."""
        return self._respond("casscf", *args, **kwargs)

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
    """Patch the backend seams with a fresh recording fake.

    Covers both acquisition paths: ``acp.backends.get_backend`` (legacy
    primitives) and ``cccp.backends.registry.get_backend`` (cccp task cores),
    plus a synthetic program-availability answer so the two-step selection
    precheck passes without real QC binaries installed.
    """
    backend = FakeBackend()

    def get_backend(name: str) -> FakeBackend:
        backend.backend_requests.append(name)
        backend.set_backend_name(name)
        return backend

    monkeypatch.setattr("acp.backends.get_backend", get_backend)
    monkeypatch.setattr("cccp.backends.registry.get_backend", get_backend)
    monkeypatch.setattr(
        "cccp.calculation.selection.resolve_executable",
        lambda name, configured_path=None: f"/synthetic/{name}",
    )
    return backend


HAS_ORCA = _REAL_QC_SNAPSHOT.available("orca")
HAS_CREST = _REAL_QC_SNAPSHOT.available("crest")
HAS_XTB = _REAL_QC_SNAPSHOT.available("xtb")
HAS_ISOSTAT = _REAL_QC_SNAPSHOT.available("isostat")
HAS_SHERMO = _REAL_QC_SNAPSHOT.available("shermo")
HAS_CENSO = _REAL_QC_SNAPSHOT.available("censo")

requires_orca = pytest.mark.skipif(not HAS_ORCA, reason=real_qc_skip_reason("orca"))
requires_crest = pytest.mark.skipif(not HAS_CREST, reason=real_qc_skip_reason("crest"))
requires_xtb = pytest.mark.skipif(not HAS_XTB, reason=real_qc_skip_reason("xtb"))
requires_isostat = pytest.mark.skipif(not HAS_ISOSTAT, reason=real_qc_skip_reason("isostat"))
requires_shermo = pytest.mark.skipif(not HAS_SHERMO, reason=real_qc_skip_reason("shermo"))
requires_censo = pytest.mark.skipif(not HAS_CENSO, reason=real_qc_skip_reason("censo"))


@pytest.fixture(scope="session")
def real_qc_snapshot() -> RealQCSnapshot:
    """The collection-time real-QC resolution snapshot (resolved once)."""
    return get_real_qc_snapshot()


@pytest.fixture(scope="session")
def real_qc_binary_path(real_qc_snapshot: RealQCSnapshot) -> Callable[[str], Path | None]:
    """Return the snapshot's resolved ABSOLUTE path for a binary key.

    Test bodies that launch a real binary MUST route through this fixture (or
    ``real_qc_snapshot.path``) so the launch path is verbatim the gate path —
    never a fresh resolution that could diverge after ``_clean_env_vars``.
    """

    def _path(name: str) -> Path | None:
        return real_qc_snapshot.path(name)

    return _path


@pytest.fixture(autouse=True)
def _clean_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delete all CONFSEARCH_* env vars before every test for isolation."""
    for var in _ALL_CONFSEARCH_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # ACP_RUN_ROOT is set by several API/scheduler tests via bare os.environ
    # (leaked into later tests, breaking handoff jobs_root() resolution).
    # Test-local overrides re-set it inside the test body.
    monkeypatch.delenv("ACP_RUN_ROOT", raising=False)
    # D03 release binding (plan todo 8): production rejects auto_sync=False
    # submissions without a verified release.  The suite models the legacy
    # unversioned shared-dir mode by default; release-binding tests opt
    # back out locally with monkeypatch.delenv (same pattern as
    # ACP_DISABLE_MPI_SNIFF below).
    monkeypatch.setenv("ACP_REMOTE_ALLOW_UNVERSIONED", "1")
    # The MPI login-shell sniff spawns `bash -lc` (cached per process);
    # disable it suite-wide so fake subprocess.run fixtures never observe
    # the sniff's probe call. Sniff-specific tests opt back out locally.
    monkeypatch.setenv("ACP_DISABLE_MPI_SNIFF", "1")


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
    if run_slow:
        return
    skip_slow = pytest.mark.skip(reason="Pass --run-slow or --run-integration to run")
    skip_real_qc = pytest.mark.skip(
        reason=(
            f"{NOT_VERIFIED}: real-QC run not requested (pass --run-slow "
            "--run-integration); skipped, never counted as a pass"
        )
    )
    for item in items:
        if not any(item.get_closest_marker(mark) for mark in ("slow", "integration")):
            continue
        is_real_qc = any(
            str(mark.kwargs.get("reason", "")).startswith(NOT_VERIFIED)
            for mark in item.iter_markers(name="skipif")
        )
        item.add_marker(skip_real_qc if is_real_qc else skip_slow)


def pytest_configure(config: pytest.Config) -> None:
    markers = [
        "slow: marks tests as slow (deselect with '-m \"not slow\"')",
        "integration: marks tests as integration tests (require external binaries)",
        "requires_orca: marks tests that need ORCA installed",
        "requires_crest: marks tests that need CREST installed",
        "requires_xtb: marks tests that need xTB installed",
        "requires_isostat: marks tests that need ISOSTAT installed",
        "requires_shermo: marks tests that need Shermo installed",
        "requires_censo: marks tests that need CENSO installed",
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)
