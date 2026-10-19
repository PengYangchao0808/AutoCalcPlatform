"""Versioned, digest-verified archive export for committed Data artifacts.

B08-3 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 44).  This
module freezes the **shape** of an archive/export package and the rules that
keep an export honest:

* a **versioned manifest** (``manifest_version``) with one entry per file:
  ``sha256`` + ``size_bytes`` + detected ``content_type`` + ``relative_path`` +
  logical ``source``;
* three **package classes** — ``full`` / ``products_only`` / ``reference`` —
  each carrying *explicit* gaps, so a ``reference`` package can never be
  mistaken for a self-contained one;
* a **consistent snapshot**: the manifest is pinned to one
  ``(generation, revision)`` fence.  If the artifacts keep changing during the
  export the builder retries, and failing that emits an explicitly *partial*
  package — it never fakes consistency;
* an **in-flight** task exports point-in-time facts only and carries **no
  execution authority**;
* **credential exclusion** and a **host-path leak scan**.  A file *extension*
  is never treated as a format contract — ``content_type`` is sniffed from the
  bytes.

Isolation (hard rule).  ``src/acp_services/**`` must never import the legacy
application package; this module is stdlib-only plus its own package's
:mod:`acp_services.data.helpers`.  The archive *source* (a task tree) is
injected through :class:`ArchiveSourcePort`; the legacy-side composition hook
lives in ``acp.services_data_bootstrap``.

Author: ACP Services (B lane, todo 44).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from acp_services.data.helpers import atomic_write_text

logger = logging.getLogger(__name__)

__all__ = [
    "ARCHIVE_MANIFEST_FILENAME",
    "ARCHIVE_MANIFEST_VERSION",
    "ArchiveDigestError",
    "ArchiveError",
    "ArchiveFileEntry",
    "ArchiveManifest",
    "ArchivePackageClass",
    "ArchiveReference",
    "ArchiveSnapshotError",
    "ArchiveSourcePort",
    "ArchiveStatus",
    "ArchiveVerification",
    "ArchiveVersionError",
    "ExecutionState",
    "LocalArchiveSource",
    "ManifestGap",
    "SnapshotFence",
    "SourceEntry",
    "build_archive_manifest",
    "detect_content_type",
    "find_host_paths",
    "is_credential_path",
    "scan_credentials",
    "scan_manifest_for_leaks",
    "verify_archive_manifest",
]

ARCHIVE_MANIFEST_VERSION = 1
ARCHIVE_MANIFEST_FILENAME = "archive_manifest.json"


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------


class ArchivePackageClass(str, Enum):
    """How much of the snapshot the package actually carries."""

    FULL = "full"
    PRODUCTS_ONLY = "products_only"
    REFERENCE = "reference"


class ArchiveStatus(str, Enum):
    """Whether the export landed on a consistent, terminal snapshot."""

    COMPLETE = "complete"
    PARTIAL = "partial"


class ExecutionState(str, Enum):
    """Whether the source task is terminal at export time."""

    COMMITTED = "committed"
    IN_FLIGHT = "in_flight"


class ArchiveError(RuntimeError):
    """Base class for archive export failures."""


class ArchiveSnapshotError(ArchiveError):
    """The pinned snapshot fence could not be satisfied (source already moved)."""


class ArchiveDigestError(ArchiveError):
    """A file no longer matches the digest/size recorded in the manifest."""


class ArchiveVersionError(ArchiveError):
    """A manifest with an unsupported ``manifest_version`` was read."""


# ---------------------------------------------------------------------------
# frozen records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SnapshotFence:
    """A single committed source position: ``(generation, revision)``.

    ``generation`` is the storage/cache generation (a purge or rewrite bumps
    it); ``revision`` is the artifact revision (for a task attempt, the attempt
    number).  Two fences compare equal only when both coordinates match.
    """

    generation: int = 0
    revision: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"generation": int(self.generation), "revision": int(self.revision)}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> SnapshotFence:
        return cls(
            generation=int(payload.get("generation", 0) or 0),
            revision=int(payload.get("revision", 0) or 0),
        )


@dataclass(frozen=True)
class SourceEntry:
    """A path the source offers for export, before credential filtering."""

    relative_path: str
    source: str
    is_product: bool = False


@dataclass(frozen=True)
class ArchiveFileEntry:
    """One bundled file: digest + size + sniffed type + logical origin."""

    relative_path: str
    sha256: str
    size_bytes: int
    content_type: str
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
            "content_type": self.content_type,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArchiveFileEntry:
        return cls(
            relative_path=str(payload.get("relative_path", "")),
            sha256=str(payload.get("sha256", "")),
            size_bytes=int(payload.get("size_bytes", 0) or 0),
            content_type=str(payload.get("content_type", "")),
            source=str(payload.get("source", "")),
        )


@dataclass(frozen=True)
class ArchiveReference:
    """An artifact named by identity but deliberately *not* bundled.

    Reference packages carry these so a consumer can fetch the artifact from
    its authoritative location and verify it against ``sha256``.  They are the
    explicit reason a reference package is **not** self-contained.
    """

    relative_path: str
    sha256: str
    size_bytes: int
    reason: str
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": int(self.size_bytes),
            "reason": self.reason,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArchiveReference:
        return cls(
            relative_path=str(payload.get("relative_path", "")),
            sha256=str(payload.get("sha256", "")),
            size_bytes=int(payload.get("size_bytes", 0) or 0),
            reason=str(payload.get("reason", "")),
            source=str(payload.get("source", "")),
        )


@dataclass(frozen=True)
class ManifestGap:
    """An explicit, machine-readable statement of what a package does not hold."""

    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "detail": self.detail}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ManifestGap:
        return cls(code=str(payload.get("code", "")), detail=str(payload.get("detail", "")))


@dataclass(frozen=True)
class ArchiveVerification:
    """Result of re-checking a manifest against its source (all passed)."""

    verified_files: int
    verified_references: int

    @property
    def ok(self) -> bool:
        return True


@dataclass(frozen=True)
class ArchiveManifest:
    """A versioned, digest-verified package manifest.

    ``self_contained`` is True only for a ``full`` package that landed on a
    stable, committed snapshot.  ``execution_authority`` is always False: an
    export never transfers the right to run or mutate the source task.
    ``point_in_time`` is True when the task was still in flight, in which case
    the manifest records facts at one instant and no more.
    """

    manifest_version: int
    package_class: ArchivePackageClass
    task_id: str
    generation: int
    revision: int
    status: ArchiveStatus
    files: tuple[ArchiveFileEntry, ...] = ()
    references: tuple[ArchiveReference, ...] = ()
    gaps: tuple[ManifestGap, ...] = ()
    excluded_paths: tuple[str, ...] = ()
    self_contained: bool = False
    execution_authority: bool = False
    point_in_time: bool = False
    export_attempts: int = 1
    created_at: str = ""

    def gap_codes(self) -> tuple[str, ...]:
        """The set of gap codes carried by this manifest."""
        return tuple(gap.code for gap in self.gaps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": int(self.manifest_version),
            "package_class": self.package_class.value,
            "task_id": self.task_id,
            "generation": int(self.generation),
            "revision": int(self.revision),
            "status": self.status.value,
            "self_contained": bool(self.self_contained),
            "execution_authority": bool(self.execution_authority),
            "point_in_time": bool(self.point_in_time),
            "export_attempts": int(self.export_attempts),
            "created_at": self.created_at,
            "files": [entry.to_dict() for entry in self.files],
            "references": [reference.to_dict() for reference in self.references],
            "gaps": [gap.to_dict() for gap in self.gaps],
            "excluded_paths": list(self.excluded_paths),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArchiveManifest:
        return cls(
            manifest_version=int(payload.get("manifest_version", 0) or 0),
            package_class=ArchivePackageClass(
                payload.get("package_class", ArchivePackageClass.FULL.value)
            ),
            task_id=str(payload.get("task_id", "")),
            generation=int(payload.get("generation", 0) or 0),
            revision=int(payload.get("revision", 0) or 0),
            status=ArchiveStatus(payload.get("status", ArchiveStatus.PARTIAL.value)),
            files=tuple(
                ArchiveFileEntry.from_dict(item) for item in payload.get("files", []) or []
            ),
            references=tuple(
                ArchiveReference.from_dict(item) for item in payload.get("references", []) or []
            ),
            gaps=tuple(ManifestGap.from_dict(item) for item in payload.get("gaps", []) or []),
            excluded_paths=tuple(str(item) for item in payload.get("excluded_paths", []) or []),
            self_contained=bool(payload.get("self_contained", False)),
            execution_authority=bool(payload.get("execution_authority", False)),
            point_in_time=bool(payload.get("point_in_time", False)),
            export_attempts=int(payload.get("export_attempts", 1) or 1),
            created_at=str(payload.get("created_at", "")),
        )

    @classmethod
    def read(cls, path: str | Path) -> ArchiveManifest:
        """Read a manifest, rejecting unsupported schema versions."""
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ArchiveVersionError("archive manifest is not a JSON object")
        version = int(payload.get("manifest_version", 0) or 0)
        if version != ARCHIVE_MANIFEST_VERSION:
            raise ArchiveVersionError(
                f"unsupported archive manifest_version {version} "
                f"(supported: {ARCHIVE_MANIFEST_VERSION})"
            )
        return cls.from_dict(payload)

    def write(self, path: str | Path) -> Path:
        """Atomically write the manifest JSON (temp file in the target dir)."""
        target = Path(path)
        atomic_write_text(target, json.dumps(self.to_dict(), indent=2, sort_keys=True, default=str))
        return target


# ---------------------------------------------------------------------------
# content sniffing — extension is NOT a format contract
# ---------------------------------------------------------------------------

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PDF_MAGIC = b"%PDF-"
_ZIP_MAGIC = b"PK\x03\x04"
_GZIP_MAGIC = b"\x1f\x8b"


def _decode_utf8(data: bytes) -> str | None:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _is_json(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def _is_xyz(text: str) -> bool:
    """True for the atom-count / comment / ``symbol x y z`` XYZ block shape."""
    lines = text.splitlines()
    if len(lines) < 3:
        return False
    try:
        count = int(lines[0].strip())
    except ValueError:
        return False
    if count <= 0:
        return False
    atom_lines = lines[2 : 2 + count]
    if len(atom_lines) < count:
        return False
    for line in atom_lines:
        parts = line.split()
        if len(parts) < 4:
            return False
        try:
            float(parts[1])
            float(parts[2])
            float(parts[3])
        except ValueError:
            return False
    return True


def _is_orca_input(text: str) -> bool:
    return any(
        stripped.startswith("!") or stripped.startswith("%")
        for stripped in (line.strip() for line in text.splitlines())
        if stripped
    )


def detect_content_type(data: bytes) -> str:
    """Sniff a content type from *bytes* only (never from a file extension)."""
    if not data:
        return "application/x-empty"
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_PDF_MAGIC):
        return "application/pdf"
    if data.startswith(_ZIP_MAGIC):
        return "application/zip"
    if data.startswith(_GZIP_MAGIC):
        return "application/gzip"
    text = _decode_utf8(data)
    if text is None:
        return "application/octet-stream"
    if _is_json(text):
        return "application/json"
    if _is_xyz(text):
        return "chemical/x-xyz"
    if _is_orca_input(text):
        return "text/x-orca-input"
    return "text/plain"


# ---------------------------------------------------------------------------
# credential + host-path exclusion
# ---------------------------------------------------------------------------

_CREDENTIAL_DIR_SEGMENTS = frozenset({".ssh", ".aws", ".gnupg", ".kube"})
_CREDENTIAL_BASENAMES = frozenset(
    {
        ".env",
        ".netrc",
        ".npmrc",
        ".pypirc",
        ".git-credentials",
        "id_rsa",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "authorized_keys",
        "credentials",
        "credential",
        "secrets",
        "secret",
        "htpasswd",
        "shadow",
        "passwd",
        "keystore",
        "token",
        "billing",
        "payment",
        "invoice",
        "card_number",
    }
)
_CREDENTIAL_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")

_CREDENTIAL_CONTENT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private_key_block"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "aws_access_key_id"),
    (re.compile(r"\bASIA[0-9A-Z]{16}\b"), "aws_session_key_id"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "github_token"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b"), "slack_token"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "api_secret_key"),
    (re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*\S+"), "password_field"),
    (re.compile(r"(?i)\bapi[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"), "api_key_field"),
    (
        re.compile(r"(?i)\bclient[_-]?secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}"),
        "client_secret_field",
    ),
    (re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+\S+"), "bearer_token"),
    (
        re.compile(r"(?i)\bcard[_-]?number\s*[:=]\s*['\"]?(?:\d[ -]?){12,19}"),
        "payment_card_number",
    ),
)

_HOST_PATH_PREFIXES = (
    "/home/",
    "/root/",
    "/etc/",
    "/var/",
    "/opt/",
    "/srv/",
    "/mnt/",
    "/Users/",
)
# Only a *token-leading* absolute path is a host path; ``WORK/opt/opt.out`` must
# not match the ``/opt/`` prefix (the lookbehind rejects a preceding path char).
_HOST_PATH_LOOKBEHIND = r"(?<![\w/.\\-])"
_WINDOWS_PATH_RE = re.compile(_HOST_PATH_LOOKBEHIND + r"[A-Za-z]:\\[^\s\"']+")


def is_credential_path(relative_path: str) -> bool:
    """True when a relative path *names* a credential store (cheap, no I/O)."""
    parts = [part.lower() for part in relative_path.replace("\\", "/").split("/") if part]
    if not parts:
        return False
    if any(segment in _CREDENTIAL_DIR_SEGMENTS for segment in parts[:-1]):
        return True
    name = parts[-1]
    if name in _CREDENTIAL_BASENAMES:
        return True
    if any(name.endswith(suffix) for suffix in _CREDENTIAL_SUFFIXES):
        return True
    return "secret" in name or "credential" in name


def scan_credentials(relative_path: str, data: bytes) -> str | None:
    """Return a reason string when *data* holds a credential, else ``None``."""
    if is_credential_path(relative_path):
        return "credential_path"
    text = _decode_utf8(data)
    if text is None:
        return None
    for pattern, reason in _CREDENTIAL_CONTENT_PATTERNS:
        if pattern.search(text):
            return reason
    return None


def find_host_paths(text: str) -> tuple[str, ...]:
    """Find directly-usable absolute host paths embedded in *text*."""
    found: list[str] = []
    for prefix in _HOST_PATH_PREFIXES:
        pattern = re.compile(_HOST_PATH_LOOKBEHIND + re.escape(prefix) + r"[^\s\"',;)\]]+")
        found.extend(match.group(0) for match in pattern.finditer(text))
    found.extend(match.group(0) for match in _WINDOWS_PATH_RE.finditer(text))
    return tuple(found)


def scan_manifest_for_leaks(manifest: ArchiveManifest) -> tuple[str, ...]:
    """Return descriptors of every credential/host-path leak in a manifest.

    An empty result is the export invariant: the builder removes credential
    files and never records absolute host paths, so this scan must stay empty.
    """
    leaks: list[str] = []

    def _scan(label: str, *values: str) -> None:
        for value in values:
            leaks.extend(f"host path in {label}: {path}" for path in find_host_paths(value))

    for entry in manifest.files:
        if is_credential_path(entry.relative_path):
            leaks.append(f"credential file: {entry.relative_path}")
        _scan("relative_path", entry.relative_path)
        _scan("source", entry.source)
    for reference in manifest.references:
        if is_credential_path(reference.relative_path):
            leaks.append(f"credential reference: {reference.relative_path}")
        _scan("reference", reference.relative_path)
    _scan("task_id", manifest.task_id)
    _scan("excluded_path", *manifest.excluded_paths)
    return tuple(leaks)


# ---------------------------------------------------------------------------
# source seam
# ---------------------------------------------------------------------------


@runtime_checkable
class ArchiveSourcePort(Protocol):
    """A pinnable view of the artifacts an export may read."""

    def fence(self) -> SnapshotFence:
        """The current committed ``(generation, revision)`` position."""

    def execution_state(self) -> ExecutionState:
        """Whether the task is terminal (``committed``) or still running."""

    def task_id(self) -> str:
        """The stable task identity recorded in the manifest."""

    def list_entries(self) -> tuple[SourceEntry, ...]:
        """Every candidate file under the fence (credential filtering is ours)."""

    def read_bytes(self, relative_path: str) -> bytes:
        """Read one candidate file's bytes (traversal-guarded)."""


def _normalize_relative_path(value: str) -> str | None:
    """Normalise to a safe POSIX relative path, or ``None`` when hostile.

    Absolute paths (POSIX or Windows) and ``..`` traversal return ``None`` so
    they can never enter a manifest — the exclusion is structural, not a filter
    applied later.
    """
    text = str(value).replace("\\", "/").strip()
    if not text:
        return None
    if text.startswith("/") or (len(text) > 1 and text[1] == ":"):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _source_label(relative_path: str) -> str:
    top = relative_path.split("/", 1)[0]
    if top == "RESULT":
        return "result"
    if top == "WORK":
        return "work"
    return "metadata"


class LocalArchiveSource:
    """An :class:`ArchiveSourcePort` over a local task tree.

    Product files are those under ``product_prefix`` (``RESULT/`` by default).
    The fence defaults to ``(0, 0)``; a real composition root passes the
    committed generation/revision (for a remote job, the cache generation and
    the attempt number).
    """

    def __init__(
        self,
        root: str | Path,
        *,
        task_id: str = "",
        generation: int = 0,
        revision: int = 0,
        execution_state: ExecutionState | str = ExecutionState.COMMITTED,
        product_prefix: str = "RESULT/",
    ) -> None:
        self._root = Path(root)
        self._task_id = task_id
        self._fence = SnapshotFence(generation=int(generation), revision=int(revision))
        self._execution_state = ExecutionState(execution_state)
        self._product_prefix = product_prefix

    @property
    def root(self) -> Path:
        return self._root

    def fence(self) -> SnapshotFence:
        return self._fence

    def execution_state(self) -> ExecutionState:
        return self._execution_state

    def task_id(self) -> str:
        return self._task_id

    def set_fence(self, generation: int, revision: int) -> None:
        """Move the source to a new committed position (composition/root use)."""
        self._fence = SnapshotFence(generation=int(generation), revision=int(revision))

    def list_entries(self) -> tuple[SourceEntry, ...]:
        if not self._root.is_dir():
            return ()
        entries: list[SourceEntry] = []
        for path in sorted(self._root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self._root).as_posix()
            entries.append(
                SourceEntry(
                    relative_path=relative,
                    source=_source_label(relative),
                    is_product=relative.startswith(self._product_prefix),
                )
            )
        return tuple(entries)

    def read_bytes(self, relative_path: str) -> bytes:
        normalized = _normalize_relative_path(relative_path)
        if normalized is None:
            raise ArchiveError(f"unsafe relative path: {relative_path!r}")
        return (self._root / normalized).read_bytes()


# ---------------------------------------------------------------------------
# manifest construction
# ---------------------------------------------------------------------------

_DESCRIPTOR_BASENAMES = frozenset(
    {
        "result_manifest.json",
        "confsearch_manifest.json",
        "task.json",
        "job.json",
        "state.json",
    }
)


def _is_descriptor(relative_path: str) -> bool:
    return relative_path.rsplit("/", 1)[-1] in _DESCRIPTOR_BASENAMES


def _export_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _collect(
    source: ArchiveSourcePort,
    package_class: ArchivePackageClass,
) -> tuple[
    tuple[ArchiveFileEntry, ...],
    tuple[ArchiveReference, ...],
    tuple[str, ...],
    int,
    int,
]:
    """Read the source once; return (files, references, excluded, omitted, unsafe)."""
    files: list[ArchiveFileEntry] = []
    references: list[ArchiveReference] = []
    excluded: list[str] = []
    omitted = 0
    unsafe = 0
    for entry in source.list_entries():
        relative = _normalize_relative_path(entry.relative_path)
        if relative is None:
            unsafe += 1
            continue
        if is_credential_path(relative):
            excluded.append(relative)
            continue
        if package_class is ArchivePackageClass.PRODUCTS_ONLY and not entry.is_product:
            omitted += 1
            continue
        data = source.read_bytes(relative)
        if scan_credentials(relative, data) is not None:
            excluded.append(relative)
            continue
        digest = hashlib.sha256(data).hexdigest()
        if package_class is ArchivePackageClass.REFERENCE and not _is_descriptor(relative):
            references.append(
                ArchiveReference(
                    relative_path=relative,
                    sha256=digest,
                    size_bytes=len(data),
                    reason="external_reference",
                    source=entry.source,
                )
            )
            continue
        files.append(
            ArchiveFileEntry(
                relative_path=relative,
                sha256=digest,
                size_bytes=len(data),
                content_type=detect_content_type(data),
                source=entry.source,
            )
        )
    files.sort(key=lambda item: item.relative_path)
    references.sort(key=lambda item: item.relative_path)
    return tuple(files), tuple(references), tuple(sorted(excluded)), omitted, unsafe


def build_archive_manifest(
    source: ArchiveSourcePort,
    *,
    package_class: ArchivePackageClass | str,
    expected_fence: SnapshotFence | None = None,
    max_attempts: int = 3,
    created_at: str | None = None,
) -> ArchiveManifest:
    """Build a versioned archive manifest over a consistent source snapshot.

    Args:
        source: The pinnable artifact source.
        package_class: ``full`` / ``products_only`` / ``reference``.
        expected_fence: When given, the export is refused unless the source is
            exactly at this fence (a fixed-generation export).  When omitted the
            builder observes a fence and accepts it only if it does not move
            while the artifacts are read.
        max_attempts: How many times to re-read when the fence moves.
        created_at: Diagnostic timestamp (tests pin it).

    Returns:
        An :class:`ArchiveManifest`.  A snapshot that never stabilises yields a
        ``partial`` package with a ``snapshot_unstable`` gap — never a fake
        complete one.  An in-flight task yields point-in-time facts with no
        execution authority.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    resolved_class = ArchivePackageClass(package_class)
    state = ExecutionState(source.execution_state())

    attempts = 0
    stable = False
    final_fence = expected_fence or source.fence()
    files: tuple[ArchiveFileEntry, ...] = ()
    references: tuple[ArchiveReference, ...] = ()
    excluded: tuple[str, ...] = ()
    omitted = 0
    unsafe = 0

    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        fence_before = source.fence()
        if expected_fence is not None and fence_before != expected_fence:
            raise ArchiveSnapshotError(
                f"source moved before export: expected {expected_fence.to_dict()}, "
                f"found {fence_before.to_dict()}"
            )
        final_fence = fence_before
        try:
            files, references, excluded, omitted, unsafe = _collect(source, resolved_class)
        except OSError:
            logger.warning("archive read failed on attempt %d; retrying", attempt, exc_info=True)
            continue
        if source.fence() == fence_before:
            stable = True
            break

    gaps: list[ManifestGap] = []
    if not stable:
        gaps.append(
            ManifestGap(
                code="snapshot_unstable",
                detail=f"artifacts changed during export after {attempts} attempt(s)",
            )
        )
    if state is ExecutionState.IN_FLIGHT:
        gaps.append(
            ManifestGap(
                code="in_flight_point_in_time",
                detail="task was not terminal at export; point-in-time facts only",
            )
        )
    if resolved_class is ArchivePackageClass.PRODUCTS_ONLY and omitted:
        gaps.append(
            ManifestGap(
                code="products_only_missing_work",
                detail=f"{omitted} non-product file(s) were not bundled",
            )
        )
    if resolved_class is ArchivePackageClass.REFERENCE:
        gaps.append(
            ManifestGap(
                code="reference_not_self_contained",
                detail=f"{len(references)} artifact(s) referenced by digest but not bundled",
            )
        )
    if unsafe:
        gaps.append(
            ManifestGap(
                code="unsafe_path_skipped",
                detail=f"{unsafe} unsafe/absolute path(s) were skipped",
            )
        )

    status = (
        ArchiveStatus.COMPLETE
        if stable and state is ExecutionState.COMMITTED
        else ArchiveStatus.PARTIAL
    )
    self_contained = (
        resolved_class is ArchivePackageClass.FULL and stable and state is ExecutionState.COMMITTED
    )
    return ArchiveManifest(
        manifest_version=ARCHIVE_MANIFEST_VERSION,
        package_class=resolved_class,
        task_id=source.task_id(),
        generation=final_fence.generation,
        revision=final_fence.revision,
        status=status,
        files=files,
        references=references,
        gaps=tuple(gaps),
        excluded_paths=excluded,
        self_contained=self_contained,
        execution_authority=False,
        point_in_time=state is ExecutionState.IN_FLIGHT,
        export_attempts=attempts,
        created_at=created_at or _export_timestamp(),
    )


def verify_archive_manifest(
    manifest: ArchiveManifest,
    source: ArchiveSourcePort,
) -> ArchiveVerification:
    """Re-read every bundled/referenced file and confirm digest + size.

    Raises:
        ArchiveDigestError: a file's bytes no longer match the manifest (the
            package has been tampered with or the source moved).
    """
    for entry in manifest.files:
        data = source.read_bytes(entry.relative_path)
        if hashlib.sha256(data).hexdigest() != entry.sha256 or len(data) != entry.size_bytes:
            raise ArchiveDigestError(
                f"digest mismatch for {entry.relative_path!r}: "
                f"expected sha256={entry.sha256} size={entry.size_bytes}"
            )
    for reference in manifest.references:
        data = source.read_bytes(reference.relative_path)
        if (
            hashlib.sha256(data).hexdigest() != reference.sha256
            or len(data) != reference.size_bytes
        ):
            raise ArchiveDigestError(
                f"digest mismatch for reference {reference.relative_path!r}: "
                f"expected sha256={reference.sha256} size={reference.size_bytes}"
            )
    return ArchiveVerification(
        verified_files=len(manifest.files),
        verified_references=len(manifest.references),
    )
