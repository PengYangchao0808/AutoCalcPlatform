"""Bounded, read-only project file access (Services/Data owner).

The Workbench project view needs one-level directory listings and bounded file
reads below a *project* root.  The project root is authoritative: it comes from
the platform's project registry (``ProjectManager``) and is never derived from a
request path.  This module is the implementation root for that surface; the
HTTP router (``acp.api.v2_routes``) only performs HTTP/auth/schema work.

Safety model (fail closed):

* the project root must be an existing directory whose resolved parent is the
  platform ``run_root`` and which is *not* ``run_root`` itself;
* request paths are strict relative POSIX paths; absolute paths, drive/UNC
  forms, backslashes, ``.``/``..``, empty segments, control characters, hidden
  segments, sensitive names and Windows reserved device names are rejected;
* symlinks/reparse points are never followed (component-wise on POSIX with
  ``dir_fd`` + ``O_NOFOLLOW``; lstat/identity-checked on other platforms);
* listings and reads are bounded: entry/scan limits for directories,
  ``max_bytes``/``max_lines`` for files; an oversized file yields a bounded
  prefix with ``truncated=True`` instead of an unbounded read;
* no absolute server path is returned and ``OSError`` text is never exposed.

Isolation (hard rule): ``src/acp_services/**`` must never import the legacy
``acp`` package, so this module is stdlib only.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_LINES",
    "DEFAULT_TREE_LIMIT",
    "DIRECTORY_SCAN_LIMIT",
    "MAX_MAX_BYTES",
    "MAX_MAX_LINES",
    "MAX_PATH_CHARS",
    "MAX_PATH_SEGMENTS",
    "MAX_TREE_LIMIT",
    "BinaryFileError",
    "ContentRejectedError",
    "FileChangedError",
    "InvalidParameterError",
    "InvalidRelativePathError",
    "NotRegularFileError",
    "ProjectFileEntry",
    "ProjectFileError",
    "ProjectFileNotFoundError",
    "ProjectFileRead",
    "ProjectRootUnavailableError",
    "ProjectTree",
    "ReadNotAllowedError",
    "VerificationBudgetExceededError",
    "list_project_directory",
    "read_project_file",
    "resolve_project_root",
]

DEFAULT_TREE_LIMIT = 200
MAX_TREE_LIMIT = 500

#: Hard scan budget per directory request (hidden/sensitive entries can never
#: exhaust the visible entry budget and make a GET unbounded).
DIRECTORY_SCAN_LIMIT = 4096

DEFAULT_MAX_BYTES = 65_536
MAX_MAX_BYTES = 262_144
DEFAULT_MAX_LINES = 1_000
MAX_MAX_LINES = 5_000

#: Verification budget for the *path string itself*.  Exceeding it is a 413,
#: not an unbounded attempt to resolve a pathological path.
MAX_PATH_CHARS = 4_096
MAX_PATH_SEGMENTS = 64
MAX_NAME_CHARS = 255

_READ_CHUNK_SIZE = 64 * 1024
_BOM = b"\xef\xbb\xbf"
_WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)
_SENSITIVE_STEMS = frozenset(
    {
        "auth",
        "authentication",
        "apikey",
        "apikeys",
        "api_key",
        "api_keys",
        "credential",
        "credentials",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "known_hosts",
        "netrc",
        "passwd",
        "password",
        "passwords",
        "privatekey",
        "private_key",
        "secret",
        "secrets",
        "shadow",
        "token",
        "tokens",
    }
)
_SENSITIVE_NAMES = frozenset(
    {
        "acp_admission.db",
        "acp_jobs.db",
        "acp_research.db",
        "jobs.db",
    }
)
_SENSITIVE_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".jks",
    ".kdbx",
    ".key",
    ".keystore",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
)

_HAS_DIR_FD = (
    os.name == "posix"
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and os.open in getattr(os, "supports_dir_fd", frozenset())
)
_O_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
_O_DIR_FLAGS_NOFOLLOW = _O_DIR_FLAGS | getattr(os, "O_NOFOLLOW", 0)
_O_FILE_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)


# ---------------------------------------------------------------------------
# Errors (stable wire codes)
# ---------------------------------------------------------------------------


class ProjectFileError(Exception):
    """Base error carrying a stable ``code``, HTTP status and safe message."""

    code = "project_file_error"
    http_status = 500

    def __init__(self, message: str = "") -> None:
        self.message = message or self.code
        super().__init__(self.message)

    def to_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


class InvalidRelativePathError(ProjectFileError):
    code = "invalid_relative_path"
    http_status = 400


class ReadNotAllowedError(ProjectFileError):
    code = "read_not_allowed"
    http_status = 403


class ProjectFileNotFoundError(ProjectFileError):
    code = "not_found"
    http_status = 404


class ProjectRootUnavailableError(ProjectFileError):
    code = "project_root_unavailable"
    http_status = 503


class FileChangedError(ProjectFileError):
    code = "file_changed"
    http_status = 409


class VerificationBudgetExceededError(ProjectFileError):
    code = "verification_budget_exceeded"
    http_status = 413


class ContentRejectedError(ProjectFileError):
    code = "unsupported_encoding"
    http_status = 415


class NotRegularFileError(ContentRejectedError):
    code = "not_regular_file"


class BinaryFileError(ContentRejectedError):
    code = "binary"


class InvalidParameterError(ProjectFileError):
    code = "invalid_parameter"
    http_status = 422


# ---------------------------------------------------------------------------
# Public value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectFileEntry:
    """One visible ordinary entry of a one-level project directory listing."""

    path: str
    name: str
    type: str
    size: int
    mtime: float


@dataclass(frozen=True)
class ProjectTree:
    """Bounded one-level project directory listing (no absolute root)."""

    project_id: str
    path: str
    entries: tuple[ProjectFileEntry, ...] = field(default_factory=tuple)
    limit: int = DEFAULT_TREE_LIMIT
    truncated: bool = False
    truncation_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "path": self.path,
            "entries": [
                {
                    "path": entry.path,
                    "name": entry.name,
                    "type": entry.type,
                    "size": entry.size,
                    "mtime": entry.mtime,
                }
                for entry in self.entries
            ],
            "limit": self.limit,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
        }


@dataclass(frozen=True)
class ProjectFileRead:
    """Bounded UTF-8 file read; ``sha256`` covers the returned bytes only."""

    project_id: str
    path: str
    content: str
    encoding: str
    size: int
    mtime: float
    returned_bytes: int
    returned_lines: int
    truncated: bool
    truncation_reason: str
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "path": self.path,
            "content": self.content,
            "encoding": self.encoding,
            "size": self.size,
            "mtime": self.mtime,
            "returned_bytes": self.returned_bytes,
            "returned_lines": self.returned_lines,
            "truncated": self.truncated,
            "truncation_reason": self.truncation_reason,
            "sha256": self.sha256,
            "sha256_scope": "returned_bytes",
        }


# ---------------------------------------------------------------------------
# Root resolution
# ---------------------------------------------------------------------------


def resolve_project_root(
    project_run_root: str | os.PathLike[str],
    platform_run_root: str | os.PathLike[str],
) -> Path:
    """Return the verified project root, or raise ``ProjectRootUnavailableError``.

    The configured project root must be a direct child directory of the
    platform ``run_root`` and must not be a symlink/reparse point.  A root that
    equals the platform root is refused.
    """
    try:
        platform = Path(platform_run_root).resolve(strict=True)
        configured = Path(project_run_root)
    except (OSError, RuntimeError, ValueError):
        raise ProjectRootUnavailableError("project root is unavailable") from None

    if not configured.is_absolute():
        raise ProjectRootUnavailableError("project root is unavailable")

    try:
        if _is_link_or_reparse(configured):
            raise ProjectRootUnavailableError("project root is unavailable")
        root = configured.resolve(strict=True)
    except ProjectRootUnavailableError:
        raise
    except (OSError, RuntimeError, ValueError):
        raise ProjectRootUnavailableError("project root is unavailable") from None

    if root == platform or root.parent != platform:
        raise ProjectRootUnavailableError("project root is unavailable")

    try:
        if not root.is_dir() or _is_link_or_reparse(root):
            raise ProjectRootUnavailableError("project root is unavailable")
    except OSError:
        raise ProjectRootUnavailableError("project root is unavailable") from None

    return root


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _has_control_chars(text: str) -> bool:
    return any(ord(char) < 0x20 or 0x7F <= ord(char) <= 0x9F for char in text)


def _is_sensitive_name(name: str) -> bool:
    lowered = name.casefold()
    if lowered in _SENSITIVE_NAMES:
        return True
    if any(lowered.endswith(suffix) for suffix in _SENSITIVE_SUFFIXES):
        return True
    stem = lowered.split(".", 1)[0]
    return stem in _SENSITIVE_STEMS


def _is_visible_segment(segment: str) -> bool:
    if not segment or segment.startswith("."):
        return False
    if segment != segment.strip() or segment.endswith("."):
        return False
    if _has_control_chars(segment):
        return False
    if ":" in segment or "\\" in segment:
        return False
    if segment.casefold() in _WINDOWS_RESERVED:
        return False
    if segment.split(".", 1)[0].casefold() in _WINDOWS_RESERVED:
        return False
    return not _is_sensitive_name(segment)


def normalize_relative_path(raw: object, *, allow_empty: bool = False) -> str:
    """Validate and return a strict relative POSIX path.

    ``""`` is accepted only for the project-root tree listing.  Any path that
    is not unambiguously inside the project root raises
    :class:`InvalidRelativePathError` (or
    :class:`VerificationBudgetExceededError` for a path larger than the
    verification budget).
    """
    if not isinstance(raw, str):
        raise InvalidRelativePathError("path must be a string")

    text = raw
    if text == "":
        if allow_empty:
            return ""
        raise InvalidRelativePathError("path is required")
    if len(text) > MAX_PATH_CHARS:
        raise VerificationBudgetExceededError("path exceeds the verification budget")
    if _has_control_chars(text):
        raise InvalidRelativePathError("path contains control characters")
    if "\\" in text:
        raise InvalidRelativePathError("path must use POSIX separators")
    if ":" in text:
        raise InvalidRelativePathError("path must not contain drive or device separators")
    if text.startswith("/") or text.startswith("//"):
        raise InvalidRelativePathError("path must be relative")
    if text in {".", ".."}:
        raise InvalidRelativePathError("path must stay inside the project root")

    segments = text.split("/")
    if len(segments) > MAX_PATH_SEGMENTS:
        raise VerificationBudgetExceededError("path exceeds the verification budget")
    for segment in segments:
        if segment in {"", ".", ".."}:
            raise InvalidRelativePathError("path contains an empty or parent segment")
        if len(segment) > MAX_NAME_CHARS:
            raise VerificationBudgetExceededError("path segment exceeds the verification budget")
        if not _is_visible_segment(segment):
            raise InvalidRelativePathError("path segment is hidden, sensitive or reserved")

    return "/".join(segments)


# ---------------------------------------------------------------------------
# Filesystem primitives
# ---------------------------------------------------------------------------


def _is_link_or_reparse(st: os.stat_result | Path) -> bool:
    try:
        result = os.stat(st, follow_symlinks=False) if isinstance(st, (str, Path)) else st
    except (OSError, TypeError, ValueError):
        return True
    if stat.S_ISLNK(result.st_mode):
        return True
    return bool(getattr(result, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT)


def _stat_identity(st: os.stat_result) -> tuple[int, int, int, int]:
    return (int(st.st_dev), int(st.st_ino), int(st.st_size), int(st.st_mtime_ns))


def _same_stat_identity(left: os.stat_result, right: os.stat_result) -> bool:
    if (left.st_dev, left.st_ino, left.st_size, left.st_mtime_ns) != (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
    ):
        return False
    # Some Windows filesystems do not populate inode numbers reliably.
    if int(left.st_ino) == 0:
        return (left.st_size, left.st_mtime_ns) == (right.st_size, right.st_mtime_ns)
    return True


def _missing(exc: OSError) -> ProjectFileError:
    if isinstance(exc, PermissionError):
        return ReadNotAllowedError("access to the project path is not allowed")
    if exc.errno in {errno.EACCES, errno.EPERM}:
        return ReadNotAllowedError("access to the project path is not allowed")
    return ProjectFileNotFoundError("project path does not exist")


def _open_directory_fd(root: Path, segments: tuple[str, ...]) -> tuple[int | None, Path]:
    """Open a directory below *root* without following links.

    On POSIX the returned descriptor is owned by the caller and every component
    is opened relative to its parent with ``O_NOFOLLOW``.  Other platforms use
    an lstat check per component and return ``(None, path)``.
    """
    if _HAS_DIR_FD:
        try:
            fd = os.open(root, _O_DIR_FLAGS_NOFOLLOW)
        except OSError:
            raise ProjectRootUnavailableError("project root is unavailable") from None
        try:
            for segment in segments:
                try:
                    child = os.open(segment, _O_DIR_FLAGS_NOFOLLOW, dir_fd=fd)
                except OSError as exc:
                    raise _missing(exc) from None
                os.close(fd)
                fd = child
            return fd, root.joinpath(*segments)
        except BaseException:
            os.close(fd)
            raise

    current = root
    for segment in segments:
        current = current / segment
        try:
            current_stat = os.stat(current, follow_symlinks=False)
        except OSError as exc:
            raise _missing(exc) from None
        if _is_link_or_reparse(current_stat) or not stat.S_ISDIR(current_stat.st_mode):
            raise ProjectFileNotFoundError("project path does not exist")
    return None, current


def _open_regular_file_fd(root: Path, segments: tuple[str, ...]) -> tuple[int, os.stat_result]:
    """Open a regular file below *root* without following links."""
    if _HAS_DIR_FD:
        dir_fd, _ = _open_directory_fd(root, segments[:-1])
        assert dir_fd is not None
        name = segments[-1]
        try:
            try:
                before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError as exc:
                raise _missing(exc) from None
            if _is_link_or_reparse(before):
                raise ProjectFileNotFoundError("project file does not exist")
            if not stat.S_ISREG(before.st_mode):
                raise NotRegularFileError("path is not a regular file")
            try:
                fd = os.open(name, _O_FILE_FLAGS, dir_fd=dir_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ProjectFileNotFoundError("project file does not exist") from None
                raise _missing(exc) from None
        finally:
            os.close(dir_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise NotRegularFileError("path is not a regular file")
            if not _same_stat_identity(before, opened):
                raise FileChangedError("project file changed while it was opened")
        except BaseException:
            os.close(fd)
            raise
        return fd, opened

    current = root.joinpath(*segments)
    path = root
    for segment in segments[:-1]:
        path = path / segment
        try:
            parent_stat = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            raise _missing(exc) from None
        if _is_link_or_reparse(parent_stat) or not stat.S_ISDIR(parent_stat.st_mode):
            raise ProjectFileNotFoundError("project path does not exist")

    try:
        before = os.stat(current, follow_symlinks=False)
    except OSError as exc:
        raise _missing(exc) from None
    if _is_link_or_reparse(before):
        raise ProjectFileNotFoundError("project file does not exist")
    if not stat.S_ISREG(before.st_mode):
        raise NotRegularFileError("path is not a regular file")

    try:
        fd = os.open(current, _O_FILE_FLAGS)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ProjectFileNotFoundError("project file does not exist") from None
        raise _missing(exc) from None
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise NotRegularFileError("path is not a regular file")
        if not _same_stat_identity(before, opened):
            raise FileChangedError("project file changed while it was opened")
    except BaseException:
        os.close(fd)
        raise
    return fd, opened


# ---------------------------------------------------------------------------
# Directory listing
# ---------------------------------------------------------------------------


def _entry_size(entry_stat: os.stat_result, entry_type: str) -> int:
    return 0 if entry_type == "directory" else int(entry_stat.st_size)


def _relative_entry_path(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def list_project_directory(
    project_id: str,
    project_run_root: str | os.PathLike[str],
    platform_run_root: str | os.PathLike[str],
    *,
    path: str = "",
    limit: int = DEFAULT_TREE_LIMIT,
) -> ProjectTree:
    """Return a bounded one-level listing of a project directory."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_TREE_LIMIT:
        raise InvalidParameterError(f"limit must be between 1 and {MAX_TREE_LIMIT}")

    relative = normalize_relative_path(path, allow_empty=True)
    root = resolve_project_root(project_run_root, platform_run_root)
    segments = tuple(relative.split("/")) if relative else ()
    dir_fd, _target = _open_directory_fd(root, segments)

    entries: list[ProjectFileEntry] = []
    scanned = 0
    scan_limit_hit = False
    try:
        if dir_fd is not None:
            iterator = os.scandir(dir_fd)
        else:
            try:
                iterator = os.scandir(root.joinpath(*segments))
            except OSError as exc:
                raise _missing(exc) from None
        with iterator:
            for os_entry in iterator:
                if scanned >= DIRECTORY_SCAN_LIMIT:
                    scan_limit_hit = True
                    break
                scanned += 1
                name = os_entry.name
                if not _is_visible_segment(name):
                    continue
                try:
                    entry_stat = os_entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link_or_reparse(entry_stat):
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    entry_type = "directory"
                elif stat.S_ISREG(entry_stat.st_mode):
                    entry_type = "file"
                else:
                    continue
                entries.append(
                    ProjectFileEntry(
                        path=_relative_entry_path(relative, name),
                        name=name,
                        type=entry_type,
                        size=_entry_size(entry_stat, entry_type),
                        mtime=float(entry_stat.st_mtime),
                    )
                )
    finally:
        if dir_fd is not None:
            os.close(dir_fd)

    entries.sort(key=lambda entry: (entry.type != "directory", entry.name.casefold(), entry.name))
    truncated = scan_limit_hit or len(entries) > limit
    truncated_entries = tuple(entries[:limit])
    if len(entries) > limit:
        reason = "entry_limit"
    elif scan_limit_hit:
        reason = "scan_limit"
    else:
        reason = ""
    return ProjectTree(
        project_id=str(project_id),
        path=relative,
        entries=truncated_entries,
        limit=limit,
        truncated=truncated,
        truncation_reason=reason,
    )


# ---------------------------------------------------------------------------
# Bounded file reads
# ---------------------------------------------------------------------------


def _read_bounded(fd: int, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most ``max_bytes`` bytes and report whether more remain."""
    chunks: list[bytes] = []
    remaining = max_bytes + 1
    while remaining > 0:
        try:
            chunk = os.read(fd, min(_READ_CHUNK_SIZE, remaining))
        except OSError as exc:
            raise _missing(exc) from None
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    return raw[:max_bytes], len(raw) > max_bytes


def _trim_incomplete_utf8_tail(data: bytes) -> bytes | None:
    """Return *data* without an incomplete UTF-8 sequence at the cut boundary."""
    try:
        data.decode("utf-8")
        return data
    except UnicodeDecodeError as exc:
        if exc.reason != "unexpected end of data" or exc.end != len(data):
            return None
        return data[: exc.start]


def _decode_bounded(raw: bytes, *, byte_truncated: bool) -> tuple[str, bool]:
    """Decode a bounded UTF-8 prefix, returning ``(text, consumed_trim)``."""
    data = raw
    if data.startswith(_BOM):
        data = data[len(_BOM) :]
    elif byte_truncated and len(data) < len(_BOM) and _BOM.startswith(data):
        # The byte cap fell inside the BOM; no complete code point is exposed.
        return "", True

    if b"\x00" in data:
        raise BinaryFileError("file contains NUL bytes")
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        if byte_truncated:
            trimmed = _trim_incomplete_utf8_tail(data)
            if trimmed is not None:
                return trimmed.decode("utf-8"), True
        raise ContentRejectedError("file is not valid UTF-8") from None


def _truncate_lines(text: str, max_lines: int) -> tuple[str, int, bool]:
    lines = text.splitlines(keepends=True)
    if not lines and text:
        return text, 1, False
    if len(lines) > max_lines:
        return "".join(lines[:max_lines]), max_lines, True
    return text, len(lines), False


def read_project_file(
    project_id: str,
    project_run_root: str | os.PathLike[str],
    platform_run_root: str | os.PathLike[str],
    *,
    path: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_lines: int = DEFAULT_MAX_LINES,
) -> ProjectFileRead:
    """Read a bounded UTF-8 prefix of one regular project file."""
    if (
        not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or not 1 <= max_bytes <= MAX_MAX_BYTES
    ):
        raise InvalidParameterError(f"max_bytes must be between 1 and {MAX_MAX_BYTES}")
    if (
        not isinstance(max_lines, int)
        or isinstance(max_lines, bool)
        or not 1 <= max_lines <= MAX_MAX_LINES
    ):
        raise InvalidParameterError(f"max_lines must be between 1 and {MAX_MAX_LINES}")

    relative = normalize_relative_path(path)
    root = resolve_project_root(project_run_root, platform_run_root)
    segments = tuple(relative.split("/"))
    fd, opened = _open_regular_file_fd(root, segments)
    try:
        raw, byte_truncated = _read_bounded(fd, max_bytes)
        try:
            after = os.fstat(fd)
        except OSError as exc:
            raise _missing(exc) from None
    finally:
        os.close(fd)

    if not _same_stat_identity(opened, after):
        raise FileChangedError("project file changed while it was read")
    if opened.st_size > max_bytes:
        byte_truncated = True

    content, boundary_trimmed = _decode_bounded(raw, byte_truncated=byte_truncated)
    content, returned_lines, line_truncated = _truncate_lines(content, max_lines)
    returned = content.encode("utf-8")
    if line_truncated:
        truncation_reason = "max_bytes_and_max_lines" if byte_truncated else "max_lines"
    elif byte_truncated or boundary_trimmed:
        truncation_reason = "max_bytes"
    else:
        truncation_reason = ""

    return ProjectFileRead(
        project_id=str(project_id),
        path=relative,
        content=content,
        encoding="utf-8",
        size=int(opened.st_size),
        mtime=float(opened.st_mtime),
        returned_bytes=len(returned),
        returned_lines=returned_lines,
        truncated=bool(byte_truncated or boundary_trimmed or line_truncated),
        truncation_reason=truncation_reason,
        sha256=hashlib.sha256(returned).hexdigest(),
    )
