"""
Centralized QC executable resolution
====================================

Single source of truth for locating computational-chemistry binaries
(ORCA, xTB, CREST, CENSO, Shermo, ISOSTAT, Molclus).  Design follows the
Grimme CENSO / MolSSI QCEngine / autodE model:

    explicit config
        -> CONFSEARCH_*_PATH env
        -> every executable hit on PATH + current Python env
        -> tiny legacy fallback list
        -> unique executable in bounded conventional install layouts
        -> None

Resolution is deliberately first-hit-wins with a fixed priority order
(explicit pin wins; no auto-prefer-newest).  *Discovery*, however, is
informational: :func:`discover_candidates` / :func:`discover_all_detailed`
enumerate *all* installs visible from each source.  A bounded filesystem
scan can resolve a missing binary only when it finds exactly one distinct
executable; ambiguous installs remain visible for an operator to pin.

Author: QCcalc Team
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static tables
# ---------------------------------------------------------------------------

#: Candidate binary names per software, in lookup order.
EXECUTABLES: dict[str, list[str]] = {
    "orca": ["orca"],
    "xtb": ["xtb"],
    "crest": ["crest"],
    "censo": ["censo"],
    "shermo": ["Shermo", "shermo"],
    "isostat": ["isostat"],
    "molclus": ["molclus"],
}

#: Environment variable override per software (legacy CONFSEARCH_*_PATH).
ENV_VARS: dict[str, str] = {
    "orca": "CONFSEARCH_ORCA_PATH",
    "xtb": "CONFSEARCH_XTB_PATH",
    "crest": "CONFSEARCH_CREST_PATH",
    "censo": "CONFSEARCH_CENSO_PATH",
    "shermo": "CONFSEARCH_SHERMO_PATH",
    "isostat": "CONFSEARCH_ISOSTAT_PATH",
    "molclus": "CONFSEARCH_MOLCLUS_PATH",
}

#: Legacy install locations — last-resort compatibility for machines that
#: predate PATH-based setup.  Never a recursive scan; 2-3 entries max.
FALLBACKS: dict[str, list[str]] = {
    "orca": ["/opt/orca/orca"],
    "xtb": ["/opt/xtb/bin/xtb", "/usr/local/bin/xtb"],
    "crest": [],
    "censo": [],
    "shermo": [],
    "isostat": [],
    "molclus": [],
}

#: Version-probe flags per software, tried in order.  Empty tuple = no probe
#: (the binary has no reliable version flag).  CENSO tries ``-v`` first
#: (modern C++ builds) then ``-version`` (CENSO-QM 1.x Python wrapper).
_VERSION_FLAGS: dict[str, tuple[str, ...]] = {
    "orca": ("--version",),
    "mpi": ("--version",),
    "xtb": ("--version",),
    "crest": ("--version",),
    "censo": ("-v", "-version"),
    "shermo": (),
    "isostat": (),
    "molclus": (),
}

#: Bounded, non-recursive conventional install layouts.  A scan result is
#: used for execution only when one distinct executable is found.  Keep
#: names exact so an unrelated binary cannot satisfy a QC requirement.
SCAN_PATTERNS: dict[str, tuple[str, ...]] = {
    "orca": (
        "/opt/orca*/orca",
        "/opt/software/orca*/orca",
        "/usr/local/orca*/orca",
        "~/orca*/orca",
    ),
    "xtb": (
        "/opt/software/xtb*/bin/xtb",
        "/opt/xtb*/bin/xtb",
        "/usr/local/xtb*/bin/xtb",
    ),
    "crest": (
        "/opt/software/crest*/crest",
        "/opt/crest*/crest",
        "/usr/local/crest*/crest",
    ),
    "censo": (
        "/opt/software/censo*/censo",
        "/opt/censo*/censo",
        "/usr/local/censo*/censo",
    ),
    "shermo": (
        "/opt/software/shermo*/Shermo",
        "/opt/software/shermo*/shermo",
        "/opt/Shermo*/Shermo",
        "/usr/local/Shermo*/Shermo",
    ),
    "isostat": ("/opt/software/molclus*/isostat", "/opt/software/isostat*/isostat"),
    "molclus": ("/opt/software/molclus*/molclus", "/opt/molclus*/molclus"),
}

#: TTL (seconds) for the module-level version-probe cache.
VERSION_CACHE_TTL = 300.0

#: Semver-like token extracted from raw version output.
_SEMVER_RE = re.compile(r"\d+(?:\.\d+)+")

#: (monotonic timestamp, normalized version) keyed by resolved absolute path.
_VERSION_CACHE: dict[str, tuple[float, str]] = {}

#: TTL (seconds) for the executable identity probe cache (never a version
#: cache: this one answers "is this really the QC program?").
EXECUTABLE_PROBE_TTL = 300.0

#: (monotonic timestamp, rejection reason or ``None``) keyed by resolved path.
_EXECUTABLE_PROBE_CACHE: dict[str, tuple[float, str | None]] = {}

#: Text markers that identify the GNOME Orca *screen reader* (a Python
#: application that owns ``/usr/bin/orca`` on desktop Linux) instead of the
#: ORCA quantum-chemistry driver.
_ORCA_SCREEN_READER_MARKERS: tuple[str, ...] = (
    "screen reader",
    "pyatspi",
    "at-spi",
    "atspi",
    "gnome orca",
)

#: A bare version line (``40.3``) is how the GNOME screen reader answers
#: ``--version``; the QC driver always prints a ``Program Version`` banner.
#: Major versions below 30 cannot collide with GNOME's 3x/4x/5x scheme and are
#: left alone so an unusual ORCA build is never rejected by shape alone.
_ORCA_BARE_VERSION_RE = re.compile(r"^v?(?P<major>\d{1,2})\.\d+(?:\.\d+)?$")

#: Bare-version majors at/above this are treated as the GNOME screen reader.
_ORCA_SCREEN_READER_MIN_MAJOR = 30


class SoftwareNotFoundError(RuntimeError):
    """Raised when a required QC binary cannot be resolved."""


class SoftwareUnavailableError(SoftwareNotFoundError):
    """A resolved candidate exists but is not the requested QC program.

    The canonical case is ``/usr/bin/orca`` on a desktop Linux host: it is the
    GNOME Orca *screen reader*, not the ORCA quantum-chemistry driver.  Running
    it would launch an accessibility application instead of a calculation, so
    resolution refuses it at the boundary and this typed error carries the
    ``rule="backend_unavailable"`` params consumed by preflight /
    ``acp doctor``: the operator must pin the real executable.
    """

    def __init__(
        self,
        message: str,
        *,
        name: str,
        executable: Path | None,
        reason: str,
    ) -> None:
        super().__init__(message)
        self.name = name
        self.executable = executable
        self.reason = reason
        self.params: dict[str, Any] = {
            "rule": "backend_unavailable",
            "field": f"executables.{name}.path",
            "software": name,
            "path": str(executable) if executable is not None else "",
            "reason": reason,
        }


@dataclass(frozen=True)
class SoftwareCandidate:
    """One discovered install of a QC executable.

    Attributes:
        path: Resolved absolute path of the executable.
        source: Where the candidate was found — one of ``"config"``,
            ``"env"``, ``"path"``, ``"fallback"`` or ``"scan"``.
    """

    path: Path
    source: str


@dataclass(frozen=True)
class SoftwareDiscovery:
    """Full discovery picture for one software package.

    Attributes:
        name: Software key from :data:`EXECUTABLES`.
        resolved: The path :func:`resolve_executable` would use, or ``None``.
        source: Source label of the resolved path, or ``None``.
        candidates: Every discovered install, de-duplicated by resolved
            path and ordered by resolution priority (config, env, PATH
            order, fallback, scan last).
    """

    name: str
    resolved: Path | None
    source: str | None
    candidates: tuple[SoftwareCandidate, ...] = field(default_factory=tuple)


def _valid_executable(path: str | Path | None) -> Path | None:
    """Return the absolute, executable file for *path*, or ``None``."""
    if not path:
        return None
    candidate = Path(path).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    return None


def _search_path() -> str:
    """PATH plus the current Python environment directory."""
    return os.pathsep.join(
        filter(None, [str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    )


def _orca_screen_reader_reason(sample: str) -> str | None:
    """Return why *sample* (``orca --version`` output) is the GNOME reader.

    Two independent signatures, both text-only (no version guessing for the
    QC driver):

    * an explicit accessibility marker (``Screen Reader`` / ``pyatspi`` /
      ``at-spi`` / ``gnome orca``);
    * a bare version line whose major is in GNOME's range (the screen reader
      answers ``40.3``; the QC driver prints a ``Program Version`` banner).

    The QC driver is never rejected on ambiguous output: probe failures and
    unrecognised banners stay "unknown", not a rejection.
    """
    lowered = sample.lower()
    for marker in _ORCA_SCREEN_READER_MARKERS:
        if marker in lowered:
            return f"GNOME Orca screen reader output contains {marker!r}"
    match = _ORCA_BARE_VERSION_RE.match(sample.strip())
    if match is not None and int(match["major"]) >= _ORCA_SCREEN_READER_MIN_MAJOR:
        return f"GNOME Orca screen reader answers --version with a bare {sample.strip()!r}"
    return None


def _probe_orca_executable(executable: Path) -> str | None:
    """Spawn ``<executable> --version`` once and classify the output."""
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env={**os.environ, "OMP_NUM_THREADS": "1"},
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("Executable identity probe failed for %s", executable)
        return None
    sample = " ".join(
        part
        for part in (getattr(result, "stdout", ""), getattr(result, "stderr", ""))
        if isinstance(part, str)
    )
    return _orca_screen_reader_reason(sample)


def probe_executable(name: str, executable: Path | None) -> str | None:
    """Return a rejection reason for a mis-identified *executable*, else None.

    Validation happens at the resolution boundary so an accessibility
    application named ``orca`` can never be handed to the QC layer.  Probe
    errors (timeout / nonzero exit / unusual banner) are *unknown*, not a
    rejection — a working ORCA build must keep resolving.  Verdicts are cached
    per resolved path for :data:`EXECUTABLE_PROBE_TTL` seconds so repeated
    submissions never re-spawn the probe.
    """
    if executable is None or name != "orca":
        return None
    try:
        stat = executable.stat()
        key = f"{executable}|{stat.st_mtime_ns}|{stat.st_size}"
    except OSError:
        key = str(executable)
    now = time.monotonic()
    cached = _EXECUTABLE_PROBE_CACHE.get(key)
    if cached is not None and now - cached[0] < EXECUTABLE_PROBE_TTL:
        return cached[1]
    reason = _probe_orca_executable(executable)
    _EXECUTABLE_PROBE_CACHE[key] = (now, reason)
    if reason is not None:
        logger.warning("Refusing 'orca' executable %s: %s", executable, reason)
    return reason


@dataclass(frozen=True)
class _ResolutionAttempt:
    """One full resolution pass: winner plus every rejected candidate."""

    path: Path | None
    source: str | None
    rejections: tuple[tuple[Path, str], ...] = ()


def _resolve_detailed(name: str, configured_path: str | Path | None) -> _ResolutionAttempt:
    """Resolution core with candidate validation — first *valid* hit wins."""
    rejections: list[tuple[Path, str]] = []
    rejected: set[Path] = set()

    def _accept(candidate: str | Path | None, source: str) -> tuple[Path, str] | None:
        path = _valid_executable(candidate)
        if path is None:
            return None
        reason = probe_executable(name, path)
        if reason is None:
            return path, source
        if path not in rejected:
            rejected.add(path)
            rejections.append((path, reason))
        return None

    # 1. Explicit configuration (absolute file path)
    hit = _accept(configured_path, "config")
    if hit is not None:
        return _ResolutionAttempt(hit[0], hit[1], tuple(rejections))

    # 2. Environment override
    env_var = ENV_VARS.get(name)
    if env_var:
        hit = _accept(os.environ.get(env_var), "env")
        if hit is not None:
            return _ResolutionAttempt(hit[0], hit[1], tuple(rejections))

    # 3. PATH + current Python environment directory.  Every hit is a
    # candidate: a rejected one (e.g. the GNOME screen reader) is skipped so
    # a later real install still wins without reordering the lookup.
    search_path = _search_path()
    for binary in EXECUTABLES.get(name, [name]):
        for found in _which_all(binary, search_path):
            hit = _accept(found, "path")
            if hit is not None:
                return _ResolutionAttempt(hit[0], hit[1], tuple(rejections))

    # 4. Legacy fallbacks
    for candidate in FALLBACKS.get(name, []):
        hit = _accept(candidate, "fallback")
        if hit is not None:
            return _ResolutionAttempt(hit[0], hit[1], tuple(rejections))

    # 5. Conventional install directories: never choose arbitrarily among
    # multiple distinct versions.  An explicit pin remains the way to select.
    valid: list[Path] = []
    for scanned in sorted(set(_scan_candidates(name))):
        hit = _accept(scanned, "scan")
        if hit is not None:
            valid.append(hit[0])
    if len(valid) == 1:
        return _ResolutionAttempt(valid[0], "scan", tuple(rejections))

    return _ResolutionAttempt(None, None, tuple(rejections))


def _resolve(name: str, configured_path: str | Path | None) -> tuple[Path | None, str | None]:
    """Resolution core — returns (path, source); first valid hit wins."""
    attempt = _resolve_detailed(name, configured_path)
    return attempt.path, attempt.source


def resolve_executable(
    name: str,
    configured_path: str | Path | None = None,
) -> Path | None:
    """Resolve the absolute path to a QC executable, or ``None``.

    Resolution order (first hit wins):

    1. **Explicit configuration** — ``configured_path`` (an absolute file
       path or, when it is a bare name, skipped so PATH handles it).
    2. **Environment override** — ``CONFSEARCH_<NAME>_PATH``.
    3. **PATH + current Python environment** — every executable hit on
       the process PATH plus the directory of ``sys.executable``, so
       conda/venv installs are found without extra machinery (a rejected
       candidate such as the GNOME Orca screen reader is skipped, not
       reordered).
    4. **Legacy fallbacks** — the small :data:`FALLBACKS` list.
    5. **Bounded scan** — one unambiguous executable in a conventional
       install directory.

    Returns the *absolute* path (never the bare command name), so callers
    can hand it straight to :func:`subprocess.run` — required by ORCA,
    whose driver locates its own parallel modules relative to the invoked
    executable.
    """
    return _resolve(name, configured_path)[0]


def resolve_executable_with_source(
    name: str,
    configured_path: str | Path | None = None,
) -> tuple[Path | None, str | None]:
    """Like :func:`resolve_executable` but also reports the winning source.

    Returns:
        ``(path, source)`` where *source* is one of ``"config"``,
        ``"env"``, ``"path"``, ``"fallback"``, ``"scan"`` — both ``None`` when the
        executable cannot be resolved.  Same priority order and semantics
        as :func:`resolve_executable`.
    """
    return _resolve(name, configured_path)


def _unavailable_error(name: str, executable: Path, reason: str) -> SoftwareUnavailableError:
    """Build the typed ``backend_unavailable`` refusal for one rejected binary."""
    display = name.upper()
    env_var = ENV_VARS.get(name)
    env_phrase = f" or set {env_var}" if env_var else ""
    return SoftwareUnavailableError(
        f"{display} executable not found: the '{name}' candidate at {executable} is "
        f"not {display} ({reason}). Set executables.{name}.path{env_phrase} to the "
        f"real {display} driver (or put it first on PATH); refusing to run the wrong "
        "executable.",
        name=name,
        executable=executable,
        reason=reason,
    )


def executable_refusal(
    name: str,
    configured_path: str | Path | None = None,
) -> SoftwareUnavailableError | None:
    """Typed refusal for the first candidate the identity probe rejected.

    Returns ``None`` when nothing was rejected (a plain not-found, or a
    resolvable binary).  Never returns a path: a caller that already holds
    ``None`` for its executable keeps that state and must not silently
    re-resolve a later-discovered binary (no execution side effects here).
    """
    attempt = _resolve_detailed(name, configured_path)
    if not attempt.rejections:
        return None
    executable, reason = attempt.rejections[0]
    return _unavailable_error(name, executable, reason)


def require_executable(
    name: str,
    configured_path: str | Path | None = None,
) -> Path:
    """Resolve *name* or raise a user-actionable typed error.

    A candidate that exists but is a different program (the GNOME Orca screen
    reader answering ``/usr/bin/orca``) raises
    :class:`SoftwareUnavailableError` (``rule="backend_unavailable"``) instead
    of being returned for execution; a genuinely missing binary raises
    :class:`SoftwareNotFoundError`.
    """
    attempt = _resolve_detailed(name, configured_path)
    if attempt.path is not None:
        return attempt.path
    if attempt.rejections:
        executable, reason = attempt.rejections[0]
        raise _unavailable_error(name, executable, reason)
    env_hint = f" set {ENV_VARS.get(name)}," if ENV_VARS.get(name) else ""
    message = (
        f"Executable '{name}' was not found. "
        f"Add '{EXECUTABLES.get(name, [name])[0]}' to PATH,{env_hint} or "
        f"configure executables.{name}.path."
    )
    raise SoftwareNotFoundError(message)


def get_configured_path(config: dict[str, Any] | None, name: str) -> str | Path | None:
    """Return the configured ``executables.<name>.path`` from *config*."""
    if not config:
        return None
    try:
        entry = config.get("executables", {}).get(name, {})
    except AttributeError:
        return None
    if isinstance(entry, dict):
        return entry.get("path")
    return None


def discover_all(config: dict[str, Any] | None = None) -> dict[str, Path | None]:
    """Resolve every known software; used by preflight/``acp doctor``."""
    return {
        name: resolve_executable(name, configured_path=get_configured_path(config, name))
        for name in EXECUTABLES
    }


def _which_all(binary: str, search_path: str) -> list[Path]:
    """Every executable hit for *binary* along *search_path*, in order.

    Uses :func:`shutil.which` per PATH entry (the historical lookup) so
    executable/extension checks stay in one place; the resolved path is then
    re-validated into an absolute file.
    """
    seen: set[Path] = set()
    hits: list[Path] = []
    for directory in search_path.split(os.pathsep):
        if not directory:
            continue
        found = shutil.which(binary, path=directory)
        if found is None:
            continue
        path = _valid_executable(found)
        if path is not None and path not in seen:
            seen.add(path)
            hits.append(path)
    return hits


def _scan_candidates(name: str) -> list[Path]:
    """Glob-based scan of conventional install dirs (informational only)."""
    hits: list[Path] = []
    for pattern in SCAN_PATTERNS.get(name, ()):
        for match in sorted(glob.glob(os.path.expanduser(pattern))):
            path = _valid_executable(match)
            if path is not None:
                hits.append(path)
    return hits


def discover_candidates(
    name: str,
    configured_path: str | Path | None = None,
) -> list[SoftwareCandidate]:
    """Enumerate *all* discoverable installs of *name* (informational).

    Candidates are de-duplicated by resolved absolute path and ordered by
    resolution priority: ``config`` (valid absolute *configured_path*),
    ``env`` (``CONFSEARCH_<NAME>_PATH``), ``path`` (every hit along the
    ``sys.executable``-dir + PATH search path, in PATH order),
    ``fallback`` (:data:`FALLBACKS` hits), then ``scan`` (:data:`SCAN_PATTERNS`
    glob hits, only for software with declared patterns).  The resolver
    accepts a scan hit only when there is one distinct executable.  A
    candidate rejected by the identity probe (the GNOME Orca screen reader at
    ``/usr/bin/orca``) is not offered as a usable install.
    """
    candidates: list[SoftwareCandidate] = []
    seen: set[Path] = set()

    def _add(path: Path | None, source: str) -> None:
        if path is not None and path not in seen:
            seen.add(path)
            if probe_executable(name, path) is not None:
                return
            candidates.append(SoftwareCandidate(path=path, source=source))

    _add(_valid_executable(configured_path), "config")

    env_var = ENV_VARS.get(name)
    if env_var:
        _add(_valid_executable(os.environ.get(env_var)), "env")

    search_path = _search_path()
    for binary in EXECUTABLES.get(name, [name]):
        for hit in _which_all(binary, search_path):
            _add(hit, "path")

    for fallback in FALLBACKS.get(name, []):
        _add(_valid_executable(fallback), "fallback")

    if name in EXECUTABLES:
        for hit in _scan_candidates(name):
            _add(hit, "scan")

    return candidates


def discover_all_detailed(config: dict[str, Any] | None = None) -> dict[str, SoftwareDiscovery]:
    """Resolved path + source + full candidate list for every known software.

    Combines :func:`resolve_executable_with_source` (the same path and
    priority order as :func:`resolve_executable`) with
    :func:`discover_candidates` for each name in :data:`EXECUTABLES`.
    Used by the ``/api/v1/software/discovery`` endpoint.
    """
    detailed: dict[str, SoftwareDiscovery] = {}
    for name in EXECUTABLES:
        configured = get_configured_path(config, name)
        resolved, source = _resolve(name, configured)
        detailed[name] = SoftwareDiscovery(
            name=name,
            resolved=resolved,
            source=source,
            candidates=tuple(discover_candidates(name, configured_path=configured)),
        )
    return detailed


def detect_version(name: str, executable: Path | None) -> str | None:
    """Best-effort version probe, decoupled from resolution.

    Tries each :data:`_VERSION_FLAGS` probe in order and returns the first
    output line of the first probe that exits 0 (a failing flag, e.g. an
    unsupported option, falls through to the next).  ``None`` when probing
    is unavailable, the binary is missing, or every probe fails.
    """
    if executable is None:
        return None
    for flag in _VERSION_FLAGS.get(name, ()):
        try:
            result = subprocess.run(
                [str(executable), flag],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "OMP_NUM_THREADS": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("Version probe failed for %s (%r)", name, flag)
            continue
        if result.returncode != 0:
            continue
        output = result.stdout or result.stderr
        first_line = output.split("\n")[0].strip()
        if first_line and any(c.isdigit() for c in first_line) and len(first_line) < 128:
            return first_line
        # The first line is blank or decorative (e.g. the xtb ASCII banner's
        # dashed separator, or CENSO 3.x with ``v 3.0.8`` on a later line) —
        # fall back to the first semver-like token anywhere in the output.
        match = _SEMVER_RE.search(output)
        if match:
            return match.group(0)
    return None


def normalize_version(raw: str | None) -> str:
    """Normalize raw version-probe output to a display version.

    Extracts the first semver-like token (``\\d+(\\.\\d+)+``) when present;
    otherwise returns the raw string truncated to 64 characters.  ``""``
    when *raw* is empty or *None*.
    """
    if not raw:
        return ""
    match = _SEMVER_RE.search(raw)
    if match:
        return match.group(0)
    return raw[:64]


def version_cached(name: str, executable: Path | None) -> str:
    """TTL-cached, normalized version probe for *executable*.

    Wraps :func:`detect_version` + :func:`normalize_version` in a
    module-level cache (:data:`VERSION_CACHE_TTL` seconds) keyed by the
    resolved absolute path, so frequently-polled callers (the backends
    API) spawn at most one probe per TTL per binary.  Failed probes are
    cached as ``""`` (negative caching, same TTL).
    """
    if executable is None:
        return ""
    key = str(executable)
    now = time.monotonic()
    entry = _VERSION_CACHE.get(key)
    if entry is not None and now - entry[0] < VERSION_CACHE_TTL:
        return entry[1]
    version = normalize_version(detect_version(name, executable))
    _VERSION_CACHE[key] = (now, version)
    return version


# ---------------------------------------------------------------------------
# MPI runtime discovery (ORCA parallel startup)
# ---------------------------------------------------------------------------
#
# ORCA's driver spawns its parallel startup helper via a *PATH lookup* of
# ``mpirun`` (``mpirun -np N <orca>/orca_startup_mpi ...``).  On stripped
# environments (systemd services, cron, bare SSH exec) the MPI bin directory
# is absent from PATH even though the ORCA binary itself resolves fine, and
# every parallel run aborts with
# ``ORCA finished by error termination in Startup``.
#
# This section discovers an ORCA-compatible MPI launcher and builds the
# subprocess environment for ORCA-family runs.  A launcher being executable
# is not sufficient: OpenMPI-linked ORCA binaries cannot be started through
# MPICH/Intel Hydra.  Explicit pins still lead, but known-incompatible pins
# are rejected; ORCA-bundled MPI is preferred over ambient PATH (especially
# conda) before login-shell/rc-file/system fallbacks are considered.


#: Binary ORCA's driver spawns for parallel runs (looked up via PATH).
MPI_BINARY = "mpirun"

#: Environment override for an explicit MPI launcher pin (see
#: :func:`resolve_mpirun`).
MPI_ENV_VAR = "CONFSEARCH_ORCA_MPI_PATH"

#: Set this env var to disable the login-shell sniff entirely (air-gapped
#: or security-hardened environments; also used to keep test fixtures that
#: stub ``subprocess.run`` from seeing the sniff's probe call).
SNIFF_DISABLE_ENV_VAR = "ACP_DISABLE_MPI_SNIFF"

#: MPI launchers bundled in an ORCA installation.  These must be considered
#: before ambient PATH because conda commonly exposes MPICH/Intel Hydra as
#: ``mpirun``, which is ABI-incompatible with OpenMPI-linked ORCA builds.
_ORCA_MPI_GLOB_PATTERNS: tuple[str, ...] = (
    "{orca_dir}/mpirun",
    "{orca_dir}/*/mpirun",
    "{orca_dir}/*/bin/mpirun",
)

#: Conventional non-ORCA OpenMPI locations used after PATH/login-shell/rc
#: candidates have been compatibility-checked.
_SYSTEM_MPI_GLOB_PATTERNS: tuple[str, ...] = (
    "~/openmpi*/bin/mpirun",
    "/opt/openmpi*/bin/mpirun",
    "/usr/lib64/openmpi/bin/mpirun",
    "/usr/lib/openmpi/bin/mpirun",
)

#: Launcher/ORCA family probe caches, keyed by resolved path/directory.
_MPI_FAMILY_CACHE: dict[str, str] = {}
_ORCA_MPI_FAMILY_CACHE: dict[str, str] = {}

#: Shell init files parsed by :func:`sniff_rc_files` (in lookup order).
_RC_FILES: tuple[str, ...] = (".bashrc", ".bash_profile", ".profile")

#: Login-shell sniff timeout (seconds).  Login shells can source arbitrary
#: rc content, so a hard cap keeps job startup bounded.
_SHELL_SNIFF_TIMEOUT = 5.0

#: bash snippet evaluated by the sniff: NUL-separated PATH, LD_LIBRARY_PATH
#: and the resolved mpirun (empty when absent).
_SNIFF_SCRIPT = (
    'printf "%s\\000%s\\000%s" "$PATH" "$LD_LIBRARY_PATH" "$(command -v mpirun 2>/dev/null)"'
)

#: ``export PATH=/dir:$PATH`` style assignments parsed from rc files.
_RC_ENV_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<var>PATH|LD_LIBRARY_PATH)\s*=\s*(?P<value>.+?)\s*(?:#.*)?$"
)


@dataclass(frozen=True)
class ShellEnvironment:
    """Environment snapshot extracted from a login shell or rc files.

    Attributes:
        path_dirs: Absolute directories from PATH, in order.
        ld_library_path_dirs: Absolute directories from LD_LIBRARY_PATH.
        mpirun: Resolved MPI launcher, when one is visible.
        source: Where the snapshot came from — ``"login-shell"`` or
            ``"rc-files"``.
    """

    path_dirs: tuple[Path, ...] = ()
    ld_library_path_dirs: tuple[Path, ...] = ()
    mpirun: Path | None = None
    source: str = "unknown"


_SHELL_ENV_CACHE: ShellEnvironment | None = None
_SHELL_ENV_SNIFFED = False


class MPICompatibilityError(SoftwareNotFoundError):
    """Raised when ORCA and the only visible MPI launcher use different ABIs."""


def _reset_shell_env_cache() -> None:
    """Drop the cached login-shell sniff (tests and re-sniff on demand)."""
    global _SHELL_ENV_CACHE, _SHELL_ENV_SNIFFED
    _SHELL_ENV_CACHE = None
    _SHELL_ENV_SNIFFED = False


def _dirs_from_env_value(value: str) -> tuple[Path, ...]:
    """Absolute directories in a ``$PATH``-style value, order preserved."""
    dirs: list[Path] = []
    for entry in value.split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        expanded = os.path.expanduser(entry)
        if not expanded.startswith("/"):
            continue
        path = Path(expanded)
        if path not in dirs:
            dirs.append(path)
    return tuple(dirs)


def sniff_login_shell_env(
    timeout: float = _SHELL_SNIFF_TIMEOUT,
) -> ShellEnvironment | None:
    """Probe what a login bash would see (``~/.bashrc``, ``module load``,
    conda init) and cache the result for the process lifetime.

    This is the primary "sniff the user's shell" mechanism: instead of
    approximating rc-file semantics, ask a real login shell for PATH,
    LD_LIBRARY_PATH and the location of :data:`MPI_BINARY`.

    Returns:
        :class:`ShellEnvironment`, or ``None`` when bash is unavailable,
        times out, or exits non-zero.  ``None`` results are cached too
        (negative caching) so per-job call sites pay the sniff at most
        once.
    """
    global _SHELL_ENV_CACHE, _SHELL_ENV_SNIFFED
    if _SHELL_ENV_SNIFFED:
        return _SHELL_ENV_CACHE
    _SHELL_ENV_SNIFFED = True
    if os.environ.get(SNIFF_DISABLE_ENV_VAR):
        logger.debug("Login-shell sniff disabled via %s", SNIFF_DISABLE_ENV_VAR)
        return None
    try:
        result = subprocess.run(
            ["bash", "-lc", _SNIFF_SCRIPT],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("Login-shell environment sniff failed", exc_info=True)
        return None
    if result.returncode != 0:
        logger.debug("Login-shell sniff exited %s", result.returncode)
        return None
    parts = (result.stdout or "").split("\x00")
    if len(parts) != 3:
        logger.debug("Login-shell sniff produced %d fields, expected 3", len(parts))
        return None
    _SHELL_ENV_CACHE = ShellEnvironment(
        path_dirs=_dirs_from_env_value(parts[0]),
        ld_library_path_dirs=_dirs_from_env_value(parts[1]),
        mpirun=_valid_executable(parts[2].strip()),
        source="login-shell",
    )
    logger.info(
        "Login-shell sniff: %d PATH dirs, mpirun=%s",
        len(_SHELL_ENV_CACHE.path_dirs),
        _SHELL_ENV_CACHE.mpirun,
    )
    return _SHELL_ENV_CACHE


def sniff_rc_files(home: Path | None = None) -> ShellEnvironment:
    """Static parse of shell init files (fallback without a login shell).

    Extracts ``PATH``/``LD_LIBRARY_PATH`` assignments whose entries are
    absolute paths (the ``dir:$PATH`` prepend idiom).  Variable references
    (``$HOME``, ``${CONDA_PREFIX}``, ...) are skipped conservatively —
    :func:`sniff_login_shell_env` handles those correctly and is the
    primary mechanism.

    Args:
        home: Home directory to read ``.bashrc``/``.bash_profile``/
            ``.profile`` from; defaults to ``Path.home()``.
    """
    home = Path.home() if home is None else Path(home)
    path_dirs: list[Path] = []
    ld_dirs: list[Path] = []
    for name in _RC_FILES:
        rc_file = home / name
        try:
            text = rc_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            match = _RC_ENV_ASSIGN_RE.match(line)
            if match is None:
                continue
            target = path_dirs if match.group("var") == "PATH" else ld_dirs
            for entry in match.group("value").split(":"):
                entry = entry.strip().strip('"').strip("'")
                if not entry or "$" in entry or not entry.startswith("/"):
                    continue
                path = Path(entry)
                if path not in target:
                    target.append(path)
    mpirun: Path | None = None
    for directory in path_dirs:
        candidate = _valid_executable(directory / MPI_BINARY)
        if candidate is not None:
            mpirun = candidate
            break
    return ShellEnvironment(
        path_dirs=tuple(path_dirs),
        ld_library_path_dirs=tuple(ld_dirs),
        mpirun=mpirun,
        source="rc-files",
    )


def _mpi_family_from_text(text: str, path: Path | None = None) -> str:
    """Classify an MPI implementation from version/ldd text and path hints."""
    haystack = text.lower()
    if path is not None:
        resolved = path.resolve()
        # Restrict path hints to the launcher and its two closest parent
        # directories.  Higher ancestors may contain unrelated words (for
        # example a job/test directory named ``openmpi-repro``) and must not
        # override an unambiguous ``mpiexec.hydra`` basename.
        path_hint = "/".join(
            (resolved.parent.parent.name, resolved.parent.name, resolved.name)
        )
        haystack = f"{haystack}\n{path_hint}".lower()
    if any(token in haystack for token in ("open mpi", "openmpi", "open-rte", "openrte")):
        return "openmpi"
    if any(token in haystack for token in ("intel(r) mpi", "intel mpi", "/oneapi/mpi", "/impi/")):
        return "intel-mpi"
    if "hydra" in haystack:
        return "hydra"
    if "mpich" in haystack:
        return "mpich"
    return "unknown"


def detect_mpi_family(executable: str | Path | None) -> str:
    """Return the MPI implementation family for a launcher.

    Path/realpath hints are checked first (important for conda's
    ``mpirun -> mpiexec.hydra`` wrapper), then ``--version`` is probed.
    Unknown implementations remain usable unless ORCA provides enough
    evidence to prove an ABI mismatch.
    """
    path = _valid_executable(executable)
    if path is None:
        return "unknown"
    key = str(path)
    cached = _MPI_FAMILY_CACHE.get(key)
    if cached is not None:
        return cached
    family = _mpi_family_from_text("", path)
    if family == "unknown":
        try:
            result = subprocess.run(
                [str(path), "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env={**os.environ, "OMP_NUM_THREADS": "1"},
            )
        except (OSError, subprocess.TimeoutExpired):
            output = ""
        else:
            output = f"{result.stdout}\n{result.stderr}"
        family = _mpi_family_from_text(output, path)
    _MPI_FAMILY_CACHE[key] = family
    return family


def _glob_mpirun(
    patterns: tuple[str, ...],
    orca_dir: str | Path | None = None,
) -> Path | None:
    """Return the first executable launcher matched by *patterns*."""
    for raw_pattern in patterns:
        pattern = raw_pattern
        if "{orca_dir}" in pattern:
            if not orca_dir:
                continue
            pattern = pattern.replace("{orca_dir}", str(orca_dir))
        for match in sorted(glob.glob(os.path.expanduser(pattern))):
            path = _valid_executable(match)
            if path is not None:
                return path
    return None


def _orca_bundled_mpirun(orca_dir: str | Path | None) -> Path | None:
    """Find the launcher shipped below an ORCA installation directory."""
    return _glob_mpirun(_ORCA_MPI_GLOB_PATTERNS, orca_dir=orca_dir)


def detect_orca_mpi_family(orca_dir: str | Path | None) -> str:
    """Infer the MPI ABI used by an ORCA installation.

    A bundled launcher is authoritative.  Otherwise, inspect dynamic links
    of a few ``*_mpi`` ORCA executables with ``ldd``.  Failure is conservative
    and returns ``"unknown"`` rather than rejecting a launcher.
    """
    if not orca_dir:
        return "unknown"
    directory = Path(orca_dir).expanduser().resolve()
    key = str(directory)
    cached = _ORCA_MPI_FAMILY_CACHE.get(key)
    if cached is not None:
        return cached

    bundled = _orca_bundled_mpirun(directory)
    family = detect_mpi_family(bundled)
    if family != "unknown":
        _ORCA_MPI_FAMILY_CACHE[key] = family
        return family

    for candidate in sorted(directory.glob("*_mpi"))[:3]:
        if not candidate.is_file():
            continue
        try:
            result = subprocess.run(
                ["ldd", str(candidate)],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        family = _mpi_family_from_text(f"{result.stdout}\n{result.stderr}")
        if family != "unknown":
            break
    _ORCA_MPI_FAMILY_CACHE[key] = family
    return family


def _mpi_families_compatible(expected: str, actual: str) -> bool:
    """Return whether two known launcher families share the MPI ABI line."""
    if "unknown" in (expected, actual):
        return True
    # OpenMPI and MPICH-derived launchers (Hydra/Intel MPI/MPICH) are not
    # interchangeable.  Variants within the MPICH-derived group are treated
    # as potentially compatible because launcher branding alone is not
    # enough to distinguish their ABI.
    return (expected == "openmpi") == (actual == "openmpi")


def resolve_mpirun(
    configured_path: str | Path | None = None,
    orca_dir: str | Path | None = None,
) -> Path | None:
    """Resolve an MPI launcher for ORCA parallel runs (first hit wins):

    1. Explicit *configured_path* (``executables.orca.mpi_path``).
    2. :data:`MPI_ENV_VAR` environment override.
    3. ORCA-bundled MPI below *orca_dir*.
    4. Current PATH (+ current Python env directory).
    5. Login-shell sniff (:func:`sniff_login_shell_env` — sees ``module
       load``, conda init and rc-file exports a service env lacks).
    6. Static rc-file parse (:func:`sniff_rc_files`).
    7. Conventional OpenMPI install globs.

    Every candidate is checked against the MPI family inferred from ORCA;
    a known OpenMPI-vs-Hydra/MPICH mismatch is skipped.
    """
    return resolve_mpirun_with_source(configured_path, orca_dir=orca_dir)[0]


def resolve_mpirun_with_source(
    configured_path: str | Path | None = None,
    orca_dir: str | Path | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve the MPI launcher and report which discovery source won.

    Uses the same compatibility-aware first-hit order as
    :func:`resolve_mpirun`.  The source is one of ``"config"``, ``"env"``,
    ``"orca-bundled"``, ``"path"``, ``"login-shell"``, ``"rc-files"`` or
    ``"scan"``.  If launchers were found but all had a known ABI mismatch,
    the path is ``None`` and source begins with ``"incompatible:"`` so the
    init wizard reports a missing runtime instead of a false positive.

    Args:
        configured_path: Explicit ``executables.orca.mpi_path`` value.
        orca_dir: Resolved ORCA installation directory, used for bundled-MPI
            fallback patterns.

    Returns:
        ``(resolved_path, source)``.
    """
    expected_family = detect_orca_mpi_family(orca_dir)
    rejected: list[tuple[Path, str]] = []

    def _accept(candidate: str | Path | None, source: str) -> tuple[Path, str] | None:
        path = _valid_executable(candidate)
        if path is None:
            return None
        family = detect_mpi_family(path)
        if not _mpi_families_compatible(expected_family, family):
            rejected.append((path, family))
            logger.warning(
                "Rejected MPI launcher %s (%s): ORCA expects %s",
                path,
                family,
                expected_family,
            )
            return None
        return path, source

    accepted = _accept(configured_path, "config")
    if accepted is not None:
        return accepted

    accepted = _accept(os.environ.get(MPI_ENV_VAR), "env")
    if accepted is not None:
        return accepted

    bundled = _orca_bundled_mpirun(orca_dir)
    accepted = _accept(bundled, "orca-bundled")
    if accepted is not None:
        return accepted

    for found in _which_all(MPI_BINARY, _search_path()):
        accepted = _accept(found, "path")
        if accepted is not None:
            return accepted

    sniffed = sniff_login_shell_env()
    if sniffed is not None:
        shell_path = os.pathsep.join(str(path) for path in sniffed.path_dirs)
        shell_candidates = _which_all(MPI_BINARY, shell_path)
        if sniffed.mpirun is not None:
            shell_candidates.insert(0, sniffed.mpirun)
        for candidate in shell_candidates:
            accepted = _accept(candidate, sniffed.source)
            if accepted is not None:
                return accepted

    rc_env = sniff_rc_files()
    rc_path = os.pathsep.join(str(path) for path in rc_env.path_dirs)
    rc_candidates = _which_all(MPI_BINARY, rc_path)
    if rc_env.mpirun is not None:
        rc_candidates.insert(0, rc_env.mpirun)
    for candidate in rc_candidates:
        accepted = _accept(candidate, rc_env.source)
        if accepted is not None:
            return accepted

    scanned = _glob_mpirun(_SYSTEM_MPI_GLOB_PATTERNS)
    accepted = _accept(scanned, "scan")
    if accepted is not None:
        return accepted
    if rejected:
        families = ",".join(sorted({family for _path, family in rejected}))
        return None, f"incompatible:{families}!={expected_family}"
    return None, None


def orca_runtime_env(
    ld_library_path: str | None = None,
    mpi_path: str | Path | None = None,
    orca_dir: str | Path | None = None,
) -> dict[str, str] | None:
    """Build the subprocess environment for ORCA-family runs.

    Resolves an MPI launcher via :func:`resolve_mpirun` and injects:

    - its bin directory at the **front of PATH** (ORCA's driver looks up
      ``mpirun`` by name — stripped service environments without the MPI
      bin dir abort every parallel run at Startup), and
    - the matching ``../lib`` directory at the front of LD_LIBRARY_PATH
      when it exists.

    Explicit *ld_library_path* (``executables.orca.ld_library_path``)
    keeps its existing override semantics.  Nothing else is merged —
    blanket-importing a login LD_LIBRARY_PATH is deliberately avoided
    because conda entries (libstdc++ et al.) are a known source of
    ABI conflicts for ORCA.

    Args:
        ld_library_path: Explicit LD_LIBRARY_PATH override, or ``None``.
        mpi_path: Explicit MPI launcher pin, or ``None``.
        orca_dir: Resolved ORCA install directory (enables bundled-MPI
            glob discovery), or ``None``.

    Returns:
        A copy of ``os.environ`` with the injections applied, or ``None``
        when nothing would change (callers pass ``env=None`` through to
        :mod:`subprocess`, preserving the inherit-everything behaviour).
    """
    mpirun, mpi_source = resolve_mpirun_with_source(mpi_path, orca_dir=orca_dir)
    if mpirun is None and mpi_source and mpi_source.startswith("incompatible:"):
        raise MPICompatibilityError(
            "No ORCA-compatible MPI launcher was found "
            f"({mpi_source.removeprefix('incompatible:')}). "
            "Conda/Intel/MPICH Hydra cannot launch an OpenMPI-linked ORCA build. "
            "Configure executables.orca.mpi_path or CONFSEARCH_ORCA_MPI_PATH "
            "to the matching OpenMPI mpirun."
        )
    if mpirun is None and not ld_library_path:
        return None

    env = dict(os.environ)
    changed = False
    if ld_library_path:
        env["LD_LIBRARY_PATH"] = str(ld_library_path)
        changed = True

    if mpirun is not None:
        bin_dir = str(mpirun.parent)
        current_path = env.get("PATH", "")
        if bin_dir not in current_path.split(os.pathsep):
            env["PATH"] = f"{bin_dir}{os.pathsep}{current_path}" if current_path else bin_dir
            logger.info("ORCA MPI runtime: prepended %s to PATH (mpirun=%s)", bin_dir, mpirun)
            changed = True
        lib_dir = mpirun.parent.parent / "lib"
        if lib_dir.is_dir():
            current_ld = env.get("LD_LIBRARY_PATH", "")
            if str(lib_dir) not in current_ld.split(os.pathsep):
                env["LD_LIBRARY_PATH"] = (
                    f"{lib_dir}{os.pathsep}{current_ld}" if current_ld else str(lib_dir)
                )
                changed = True

    return env if changed else None


__all__ = [
    "ENV_VARS",
    "EXECUTABLES",
    "FALLBACKS",
    "MPI_BINARY",
    "MPICompatibilityError",
    "MPI_ENV_VAR",
    "SCAN_PATTERNS",
    "SNIFF_DISABLE_ENV_VAR",
    "VERSION_CACHE_TTL",
    "ShellEnvironment",
    "SoftwareCandidate",
    "SoftwareDiscovery",
    "SoftwareNotFoundError",
    "detect_version",
    "detect_mpi_family",
    "detect_orca_mpi_family",
    "discover_all",
    "discover_all_detailed",
    "discover_candidates",
    "get_configured_path",
    "normalize_version",
    "orca_runtime_env",
    "resolve_executable",
    "resolve_executable_with_source",
    "resolve_mpirun",
    "resolve_mpirun_with_source",
    "sniff_login_shell_env",
    "sniff_rc_files",
    "version_cached",
]
