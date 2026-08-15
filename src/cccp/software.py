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

There is deliberately *no* directory scanning, provider framework,
candidate ranking or discovery cache: the OS PATH *is* the discovery
protocol for the QC software ecosystem.  Every consumer (backends, API,
catalog, preflight, workflows) must resolve binaries through
:func:`resolve_executable` — the one place discovery happens.

Author: QCcalc Team
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
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


class SoftwareNotFoundError(RuntimeError):
    """Raised when a required QC binary cannot be resolved."""


def _valid_executable(path: str | Path | None) -> Path | None:
    """Return the absolute, executable file for *path*, or ``None``."""
    if not path:
        return None
    candidate = Path(path).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate.resolve()
    return None


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
    # 1. Explicit configuration (absolute file path)
    path = _valid_executable(configured_path)
    if path:
        return path

    # 2. Environment override
    env_var = ENV_VARS.get(name)
    if env_var:
        path = _valid_executable(os.environ.get(env_var))
        if path:
            return path

    # 3. PATH + current Python environment directory
    search_path = os.pathsep.join(
        filter(None, [str(Path(sys.executable).parent), os.environ.get("PATH", "")])
    )
    for binary in EXECUTABLES.get(name, [name]):
        found = shutil.which(binary, path=search_path)
        if found:
            return Path(found).resolve()

    # 4. Legacy fallbacks
    for candidate in FALLBACKS.get(name, []):
        path = _valid_executable(candidate)
        if path:
            return path

    return None


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
        first_line = (result.stdout or result.stderr).split("\n")[0].strip()
        if first_line and len(first_line) < 128:
            return first_line
    return None


__all__ = [
    "ENV_VARS",
    "EXECUTABLES",
    "FALLBACKS",
    "SoftwareNotFoundError",
    "detect_version",
    "discover_all",
    "get_configured_path",
    "require_executable",
    "resolve_executable",
]
