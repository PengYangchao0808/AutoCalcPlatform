"""
Centralized QC executable resolution
====================================

Single source of truth for locating computational-chemistry binaries
(ORCA, xTB, CREST, CENSO, Shermo, ISOSTAT, Molclus).  Design follows the
Grimme CENSO / MolSSI QCEngine / autodE model:

    explicit config
        -> CONFSEARCH_*_PATH env
        -> shutil.which() over PATH + current Python env
        -> tiny legacy fallback list
        -> None

Resolution is deliberately first-hit-wins with a fixed priority order
(explicit pin wins; no auto-prefer-newest).  *Discovery*, however, is
informational: :func:`discover_candidates` / :func:`discover_all_detailed`
enumerate *all* installs visible from each source (including a small
glob-based filesystem scan of conventional install dirs) so the API can
surface multi-install situations.  Discovery never feeds back into
resolution order.

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
    "xtb": ("--version",),
    "crest": ("--version",),
    "censo": ("-v", "-version"),
    "shermo": (),
    "isostat": (),
    "molclus": (),
}

#: Glob patterns for the informational filesystem scan (discovery only —
#: never consulted by :func:`resolve_executable`).  Only patterns that are
#: trivially safe (conventional, non-recursive install layouts) belong here;
#: software without such a layout simply has no entry.  Missing or
#: unreadable directories are skipped silently.
SCAN_PATTERNS: dict[str, tuple[str, ...]] = {
    "orca": (
        "/opt/orca*/orca",
        "/opt/software/orca*/orca",
        "/usr/local/orca*/orca",
        "~/orca*/orca",
    ),
}

#: TTL (seconds) for the module-level version-probe cache.
VERSION_CACHE_TTL = 300.0

#: Semver-like token extracted from raw version output.
_SEMVER_RE = re.compile(r"\d+(?:\.\d+)+")

#: (monotonic timestamp, normalized version) keyed by resolved absolute path.
_VERSION_CACHE: dict[str, tuple[float, str]] = {}


class SoftwareNotFoundError(RuntimeError):
    """Raised when a required QC binary cannot be resolved."""


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


def _resolve(name: str, configured_path: str | Path | None) -> tuple[Path | None, str | None]:
    """Resolution core — returns (path, source); first hit wins."""
    # 1. Explicit configuration (absolute file path)
    path = _valid_executable(configured_path)
    if path:
        return path, "config"

    # 2. Environment override
    env_var = ENV_VARS.get(name)
    if env_var:
        path = _valid_executable(os.environ.get(env_var))
        if path:
            return path, "env"

    # 3. PATH + current Python environment directory
    search_path = _search_path()
    for binary in EXECUTABLES.get(name, [name]):
        found = shutil.which(binary, path=search_path)
        if found:
            return Path(found).resolve(), "path"

    # 4. Legacy fallbacks
    for candidate in FALLBACKS.get(name, []):
        path = _valid_executable(candidate)
        if path:
            return path, "fallback"

    return None, None


def resolve_executable(
    name: str,
    configured_path: str | Path | None = None,
) -> Path | None:
    """Resolve the absolute path to a QC executable, or ``None``.

    Resolution order (first hit wins):

    1. **Explicit configuration** — ``configured_path`` (an absolute file
       path or, when it is a bare name, skipped so PATH handles it).
    2. **Environment override** — ``CONFSEARCH_<NAME>_PATH``.
    3. **PATH + current Python environment** — :func:`shutil.which` over
       the process PATH plus the directory of ``sys.executable``, so
       conda/venv installs are found without extra machinery.
    4. **Legacy fallbacks** — the small :data:`FALLBACKS` list.

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
        ``"env"``, ``"path"``, ``"fallback"`` — both ``None`` when the
        executable cannot be resolved.  Same priority order and semantics
        as :func:`resolve_executable`.
    """
    return _resolve(name, configured_path)


def require_executable(
    name: str,
    configured_path: str | Path | None = None,
) -> Path:
    """Resolve *name* or raise :class:`SoftwareNotFoundError`.

    The raised error carries a user-actionable message (add to PATH, set
    the env var, or configure ``executables.<name>.path``).
    """
    path = resolve_executable(name, configured_path=configured_path)
    if path is None:
        env_hint = f" set {ENV_VARS.get(name)}," if ENV_VARS.get(name) else ""
        message = (
            f"Executable '{name}' was not found. "
            f"Add '{EXECUTABLES.get(name, [name])[0]}' to PATH,{env_hint} or "
            f"configure executables.{name}.path."
        )
        raise SoftwareNotFoundError(message)
    return path


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
    """Every executable hit for *binary* along *search_path*, in order."""
    seen: set[Path] = set()
    hits: list[Path] = []
    for directory in search_path.split(os.pathsep):
        if not directory:
            continue
        path = _valid_executable(Path(directory) / binary)
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
    glob hits, only for software with declared patterns).  This never
    affects :func:`resolve_executable` — the first-hit-wins resolution
    semantics are unchanged.
    """
    candidates: list[SoftwareCandidate] = []
    seen: set[Path] = set()

    def _add(path: Path | None, source: str) -> None:
        if path is not None and path not in seen:
            seen.add(path)
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
        if first_line and len(first_line) < 128:
            return first_line
        # The first line is blank or decorative (e.g. CENSO 3.x prints an
        # ASCII banner with ``v 3.0.8`` on a later line) — fall back to the
        # first semver-like token anywhere in the output.
        match = _SEMVER_RE.search(output)
        if match:
            return match.group(0)
    return None


def normalize_version(raw: str | None) -> str:
    """Normalize raw version-probe output to a display version.

    Extracts the first semver-like token (``\\d+(\\.\\d+)+``) when present;
    otherwise returns the raw string truncated to 64 characters.  ``""``
    when *raw* is empty or ``None``.
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


__all__ = [
    "ENV_VARS",
    "EXECUTABLES",
    "FALLBACKS",
    "SCAN_PATTERNS",
    "VERSION_CACHE_TTL",
    "SoftwareCandidate",
    "SoftwareDiscovery",
    "SoftwareNotFoundError",
    "detect_version",
    "discover_all",
    "discover_all_detailed",
    "discover_candidates",
    "get_configured_path",
    "normalize_version",
    "require_executable",
    "resolve_executable",
    "resolve_executable_with_source",
    "version_cached",
]
