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
# This section discovers a usable MPI launcher and builds the subprocess
# environment for ORCA-family runs:  explicit pin -> env var -> PATH ->
# login-shell sniff (``bash -lc`` — sees ``module load``, conda init and
# rc-file exports) -> static rc-file parse -> conventional install globs.


#: Binary ORCA's driver spawns for parallel runs (looked up via PATH).
MPI_BINARY = "mpirun"

#: Environment override for an explicit MPI launcher pin (see
#: :func:`resolve_mpirun`).
MPI_ENV_VAR = "CONFSEARCH_ORCA_MPI_PATH"

#: Set this env var to disable the login-shell sniff entirely (air-gapped
#: or security-hardened environments; also used to keep test fixtures that
#: stub ``subprocess.run`` from seeing the sniff's probe call).
SNIFF_DISABLE_ENV_VAR = "ACP_DISABLE_MPI_SNIFF"

#: Glob patterns for last-resort MPI discovery.  ``{orca_dir}`` is replaced
#: with the resolved ORCA install directory when available.
_MPI_GLOB_PATTERNS: tuple[str, ...] = (
    "{orca_dir}/mpirun",
    "{orca_dir}/*/mpirun",
    "{orca_dir}/*/bin/mpirun",
    "~/openmpi*/bin/mpirun",
    "/opt/openmpi*/bin/mpirun",
    "/usr/lib64/openmpi/bin/mpirun",
    "/usr/lib/openmpi/bin/mpirun",
)

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


def resolve_mpirun(
    configured_path: str | Path | None = None,
    orca_dir: str | Path | None = None,
) -> Path | None:
    """Resolve an MPI launcher for ORCA parallel runs (first hit wins):

    1. Explicit *configured_path* (``executables.orca.mpi_path``).
    2. :data:`MPI_ENV_VAR` environment override.
    3. Current PATH (+ current Python env directory).
    4. Login-shell sniff (:func:`sniff_login_shell_env` — sees ``module
       load``, conda init and rc-file exports a service env lacks).
    5. Static rc-file parse (:func:`sniff_rc_files`).
    6. Conventional install globs (:data:`_MPI_GLOB_PATTERNS`), including
       bundled MPI inside *orca_dir*.
    """
    path = _valid_executable(configured_path)
    if path:
        return path

    path = _valid_executable(os.environ.get(MPI_ENV_VAR))
    if path:
        return path

    found = shutil.which(MPI_BINARY, path=_search_path())
    if found:
        return Path(found).resolve()

    sniffed = sniff_login_shell_env()
    if sniffed is not None and sniffed.mpirun is not None:
        return sniffed.mpirun

    rc_env = sniff_rc_files()
    if rc_env.mpirun is not None:
        return rc_env.mpirun

    for pattern in _MPI_GLOB_PATTERNS:
        if "{orca_dir}" in pattern:
            if not orca_dir:
                continue
            pattern = pattern.replace("{orca_dir}", str(orca_dir))
        for match in sorted(glob.glob(os.path.expanduser(pattern))):
            path = _valid_executable(match)
            if path:
                return path
    return None


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
    mpirun = resolve_mpirun(mpi_path, orca_dir=orca_dir)
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
    "MPI_ENV_VAR",
    "SCAN_PATTERNS",
    "SNIFF_DISABLE_ENV_VAR",
    "VERSION_CACHE_TTL",
    "ShellEnvironment",
    "SoftwareCandidate",
    "SoftwareDiscovery",
    "SoftwareNotFoundError",
    "detect_version",
    "discover_all",
    "discover_all_detailed",
    "discover_candidates",
    "get_configured_path",
    "normalize_version",
    "orca_runtime_env",
    "resolve_executable",
    "resolve_executable_with_source",
    "resolve_mpirun",
    "sniff_login_shell_env",
    "sniff_rc_files",
    "version_cached",
]
