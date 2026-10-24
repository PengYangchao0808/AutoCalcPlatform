"""ACP2 artifact-snapshot content reader (A-12a).

An ACP2 science run consumes an immutable input snapshot prepared by the
platform's Data service.  The platform owns the snapshot envelope: it decides
which run may be exported, whether an export is still permitted and which
generation is current.  This module deliberately knows none of that.  It reads
the snapshot only as a *science content* document and answers one question:

    Do the declared artifacts, their bytes and their scientific identity agree?

Responsibilities are split on purpose:

* **Platform boundary (harness, not CCCP).**  ``scripts/acceptance/
  acp2_science.py`` checks the platform binding and the export envelope of the
  snapshot before any scientific consumer is invoked.
* **Content boundary (this module).**  It validates the declared artifact
  roles, each artifact digest against the bytes below the declared read root,
  the snapshot schema/version/generation as opaque provenance, and the
  scientific identity (structure digest, method, electronic state, energy unit
  and atom mapping).

Hard rules:

* paths from the document are always resolved **below** ``read_root``; an
  absolute or escaping artifact path is rejected, never trusted;
* a platform envelope (``platform``) is carried through verbatim and is never
  interpreted here;
* importing this module imports neither the platform services package nor the
  legacy application package, and starts no subprocess.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Final

from cccp.analysis.research._common import (
    ScienceContractError,
    canonical_json,
    require_int,
    require_mapping,
    require_sequence,
    require_string,
)
from cccp.analysis.research.question import ENERGY_UNITS, ElectronicStateRef
from cccp.analysis.research.reaction import AtomMapping

logger = logging.getLogger(__name__)

__all__ = [
    "ARTIFACT_ROLES",
    "SNAPSHOT_SCHEMA",
    "SNAPSHOT_SCHEMA_VERSION",
    "STRUCTURE_ROLES",
    "ArtifactContentUnavailableError",
    "ArtifactDigest",
    "ArtifactDigestMismatchError",
    "ArtifactPathEscapeError",
    "ArtifactRef",
    "ArtifactScienceIdentity",
    "ArtifactScienceIdentityError",
    "ArtifactSnapshot",
    "ArtifactSnapshotError",
    "ArtifactStructuralMismatchError",
    "UnsupportedSnapshotSchemaVersionError",
    "load_verified_snapshot",
]

#: Snapshot document identifier this reader accepts.
SNAPSHOT_SCHEMA: Final = "acp2.science.artifact-snapshot"
#: Only major version whose structure is readable by this module.
SNAPSHOT_SCHEMA_VERSION: Final = 1

#: Roles a snapshot may declare for one artifact.  The role is a declaration
#: by the producer; this reader only checks that it is a known role.
ARTIFACT_ROLES: Final = frozenset(
    {
        "input_structure",
        "optimized_structure",
        "structure",
        "frequency_output",
        "singlepoint_output",
        "hessian",
        "native_output",
        "provenance",
    }
)
#: Roles that may satisfy the scientific structure digest.
STRUCTURE_ROLES: Final = frozenset({"input_structure", "optimized_structure", "structure"})

_SNAPSHOT_KEYS: Final = frozenset(
    {
        "snapshot_id",
        "snapshot_schema",
        "snapshot_schema_version",
        "generation",
        "read_root",
        "scientific_identity",
        "artifacts",
        "platform",
    }
)
_ARTIFACT_KEYS: Final = frozenset({"role", "path", "sha256", "size_bytes", "media_type"})
_IDENTITY_KEYS: Final = frozenset(
    {
        "structure_sha256",
        "method",
        "electronic_state",
        "energy_unit",
        "atom_mapping",
        "basis",
    }
)

_DIGEST_PATTERN: Final = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")


# ── typed errors ────────────────────────────────────────────────────────────


class ArtifactSnapshotError(ScienceContractError):
    """Base class for a rejected artifact snapshot."""


class ArtifactStructuralMismatchError(ArtifactSnapshotError):
    """The snapshot shape, artifact role or declared schema is not readable."""


class UnsupportedSnapshotSchemaVersionError(ArtifactStructuralMismatchError):
    """The snapshot declares a schema major version this reader cannot read."""


class ArtifactPathEscapeError(ArtifactSnapshotError):
    """An artifact path is absolute or resolves outside the declared read root."""


class ArtifactDigestMismatchError(ArtifactSnapshotError):
    """An artifact's bytes do not match its declared digest or size."""


class ArtifactContentUnavailableError(ArtifactSnapshotError):
    """A declared artifact is absent or unreadable below the read root."""


class ArtifactScienceIdentityError(ArtifactSnapshotError):
    """The declared scientific identity is malformed or not tied to an artifact."""


# ── small typed helpers ─────────────────────────────────────────────────────


def _as_mapping(value: Any, where: str) -> dict[str, Any]:
    try:
        return require_mapping(value, where)
    except ScienceContractError as exc:
        raise ArtifactStructuralMismatchError(f"{where}: {exc}") from exc


def _as_sequence(value: Any, where: str) -> list[Any]:
    try:
        return require_sequence(value, where)
    except ScienceContractError as exc:
        raise ArtifactStructuralMismatchError(f"{where}: {exc}") from exc


def _as_string(value: Any, where: str) -> str:
    try:
        return require_string(value, where)
    except ScienceContractError as exc:
        raise ArtifactStructuralMismatchError(f"{where}: {exc}") from exc


def _as_int(value: Any, where: str, *, minimum: int | None = None) -> int:
    try:
        return require_int(value, where, minimum=minimum)
    except ScienceContractError as exc:
        raise ArtifactStructuralMismatchError(f"{where}: {exc}") from exc


def _normalise_digest(value: Any, where: str) -> str:
    if not isinstance(value, str):
        raise ArtifactStructuralMismatchError(
            f"{where}: expected a sha256 digest string, got {type(value).__name__}"
        )
    match = _DIGEST_PATTERN.fullmatch(value)
    if match is None:
        raise ArtifactStructuralMismatchError(
            f"{where}: expected sha256:<64 hex> (or bare 64 hex), got {value!r}"
        )
    return f"sha256:{match.group(1).lower()}"


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ArtifactStructuralMismatchError(
            f"{where}: unknown field(s) {unknown}; the artifact snapshot contract is closed"
        )


# ── artifact role and reference ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """One declared artifact resolved below the snapshot's read root.

    ``relative_path`` is kept exactly as declared (POSIX form); the absolute
    ``resolved_path`` is derived locally and is never read from the document.
    """

    role: str
    relative_path: str
    declared_sha256: str
    resolved_path: Path
    size_bytes: int | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        if self.role not in ARTIFACT_ROLES:
            raise ArtifactStructuralMismatchError(
                f"artifact.role must be one of {sorted(ARTIFACT_ROLES)}, got {self.role!r}"
            )
        object.__setattr__(
            self, "declared_sha256", _normalise_digest(self.declared_sha256, "artifact.sha256")
        )
        if self.size_bytes is not None:
            object.__setattr__(
                self,
                "size_bytes",
                _as_int(self.size_bytes, "artifact.size_bytes", minimum=0),
            )
        if self.media_type is not None:
            object.__setattr__(
                self, "media_type", _as_string(self.media_type, "artifact.media_type")
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the declared, path-relative form (never the resolved path)."""
        entry: dict[str, Any] = {
            "role": self.role,
            "path": self.relative_path,
            "sha256": self.declared_sha256,
        }
        if self.size_bytes is not None:
            entry["size_bytes"] = self.size_bytes
        if self.media_type is not None:
            entry["media_type"] = self.media_type
        return entry


@dataclass(frozen=True, slots=True)
class ArtifactDigest:
    """One verified artifact: its declared identity and its actual bytes."""

    role: str
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        """Return a deterministic digest-table row."""
        return {
            "role": self.role,
            "path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


def _resolve_artifact_path(read_root: Path, raw_path: Any, where: str) -> tuple[str, Path]:
    if not isinstance(raw_path, str) or not raw_path:
        raise ArtifactStructuralMismatchError(f"{where}: path must be a non-empty string")
    pure = PurePosixPath(raw_path)
    if pure.is_absolute() or Path(raw_path).is_absolute():
        raise ArtifactPathEscapeError(
            f"{where}: absolute artifact path {raw_path!r} is never trusted; "
            "paths are resolved below read_root"
        )
    if pure.as_posix() in ("", "."):
        raise ArtifactStructuralMismatchError(f"{where}: path must name a file below read_root")
    resolved = (read_root / Path(*pure.parts)).resolve()
    try:
        resolved.relative_to(read_root)
    except ValueError as exc:
        raise ArtifactPathEscapeError(
            f"{where}: artifact path {raw_path!r} resolves outside the declared read root"
        ) from exc
    return pure.as_posix(), resolved


# ── scientific identity ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArtifactScienceIdentity:
    """Scientific identity that ties a snapshot to one structure and method.

    This is a comparability declaration, not a second method or spin model:
    it reuses :class:`ElectronicStateRef` for charge/multiplicity and
    :class:`AtomMapping` for the atom correspondence.
    """

    structure_sha256: str
    method: str
    electronic_state: ElectronicStateRef
    energy_unit: str
    atom_mapping: AtomMapping | None = None
    basis: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "structure_sha256",
            _normalise_digest(self.structure_sha256, "scientific_identity.structure_sha256"),
        )
        if not isinstance(self.method, str) or not self.method:
            raise ArtifactScienceIdentityError(
                "scientific_identity.method must be a non-empty string"
            )
        state = self.electronic_state
        if not isinstance(state, ElectronicStateRef):
            if not isinstance(state, Mapping):
                raise ArtifactScienceIdentityError(
                    "scientific_identity.electronic_state must be a JSON object"
                )
            try:
                state = ElectronicStateRef.from_dict(state, "scientific_identity.electronic_state")
            except ScienceContractError as exc:
                raise ArtifactScienceIdentityError(
                    f"scientific_identity.electronic_state: {exc}"
                ) from exc
        object.__setattr__(self, "electronic_state", state)
        if self.energy_unit not in ENERGY_UNITS:
            raise ArtifactScienceIdentityError(
                "scientific_identity.energy_unit must be one of "
                f"{sorted(ENERGY_UNITS)}, got {self.energy_unit!r}"
            )
        mapping = self.atom_mapping
        if mapping is not None and not isinstance(mapping, AtomMapping):
            if not isinstance(mapping, Mapping):
                raise ArtifactScienceIdentityError(
                    "scientific_identity.atom_mapping must be a JSON object or null"
                )
            try:
                mapping = AtomMapping.from_dict(mapping, "scientific_identity.atom_mapping")
            except ScienceContractError as exc:
                raise ArtifactScienceIdentityError(
                    f"scientific_identity.atom_mapping: {exc}"
                ) from exc
        object.__setattr__(self, "atom_mapping", mapping)
        if self.basis is not None and (not isinstance(self.basis, str) or not self.basis):
            raise ArtifactScienceIdentityError(
                "scientific_identity.basis must be a non-empty string or null"
            )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical, JSON-safe identity view."""
        return {
            "structure_sha256": self.structure_sha256,
            "method": self.method,
            "electronic_state": self.electronic_state.to_dict(),
            "energy_unit": self.energy_unit,
            "atom_mapping": self.atom_mapping.to_dict() if self.atom_mapping else None,
            "basis": self.basis,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], where: str = "scientific_identity"
    ) -> ArtifactScienceIdentity:
        """Build the identity from its declared mapping, rejecting unknown fields."""
        data = _as_mapping(payload, where)
        _reject_unknown(data, _IDENTITY_KEYS, where)
        for required in ("structure_sha256", "method", "electronic_state", "energy_unit"):
            if required not in data:
                raise ArtifactScienceIdentityError(f"{where}: missing required field {required!r}")
        return cls(
            structure_sha256=data["structure_sha256"],
            method=data["method"],
            electronic_state=data["electronic_state"],
            energy_unit=data["energy_unit"],
            atom_mapping=data.get("atom_mapping"),
            basis=data.get("basis"),
        )


# ── snapshot ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    """A parsed artifact snapshot with every path resolved below its root."""

    snapshot_id: str
    snapshot_schema: str
    snapshot_schema_version: int
    generation: str | int
    read_root: Path
    science_identity: ArtifactScienceIdentity
    artifacts: tuple[ArtifactRef, ...]
    platform: dict[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id:
            raise ArtifactStructuralMismatchError("snapshot.snapshot_id must be a non-empty string")
        if self.snapshot_schema != SNAPSHOT_SCHEMA:
            raise ArtifactStructuralMismatchError(
                "snapshot.snapshot_schema must be "
                f"{SNAPSHOT_SCHEMA!r}, got {self.snapshot_schema!r}"
            )
        if (
            isinstance(self.snapshot_schema_version, bool)
            or not isinstance(self.snapshot_schema_version, int)
            or self.snapshot_schema_version != SNAPSHOT_SCHEMA_VERSION
        ):
            raise UnsupportedSnapshotSchemaVersionError(
                f"unsupported snapshot schema_version {self.snapshot_schema_version!r}; "
                f"only {SNAPSHOT_SCHEMA_VERSION} is readable"
            )
        if isinstance(self.generation, bool) or not isinstance(self.generation, (str, int)):
            raise ArtifactStructuralMismatchError(
                "snapshot.generation must be a string or integer provenance token"
            )
        if isinstance(self.generation, str) and not self.generation:
            raise ArtifactStructuralMismatchError("snapshot.generation must not be empty")
        if not isinstance(self.read_root, Path):
            raise ArtifactStructuralMismatchError("snapshot.read_root must be a resolved Path")
        if not isinstance(self.science_identity, ArtifactScienceIdentity):
            raise ArtifactStructuralMismatchError(
                "snapshot.scientific_identity must be an ArtifactScienceIdentity"
            )
        if not isinstance(self.artifacts, tuple) or not self.artifacts:
            raise ArtifactStructuralMismatchError("snapshot.artifacts must be a non-empty tuple")
        if not isinstance(self.platform, dict):
            raise ArtifactStructuralMismatchError("snapshot.platform must be a JSON object")

    def verify_content(self) -> tuple[ArtifactDigest, ...]:
        """Hash every artifact below the read root and compare declared digests."""
        rows: list[ArtifactDigest] = []
        for artifact in self.artifacts:
            try:
                size = artifact.resolved_path.stat().st_size
                actual = _hash_file(artifact.resolved_path)
            except OSError as exc:
                raise ArtifactContentUnavailableError(
                    f"{artifact.relative_path}: cannot read declared artifact: {exc}"
                ) from exc
            if artifact.size_bytes is not None and size != artifact.size_bytes:
                raise ArtifactDigestMismatchError(
                    f"{artifact.relative_path}: declared size {artifact.size_bytes} "
                    f"but found {size} bytes"
                )
            digest = f"sha256:{actual}"
            if digest != artifact.declared_sha256:
                raise ArtifactDigestMismatchError(
                    f"{artifact.relative_path}: declared {artifact.declared_sha256} "
                    f"but content is {digest}"
                )
            rows.append(
                ArtifactDigest(
                    role=artifact.role,
                    relative_path=artifact.relative_path,
                    sha256=digest,
                    size_bytes=size,
                )
            )
        return tuple(rows)

    def digest_table(self) -> list[dict[str, Any]]:
        """Return the sorted, JSON-safe digest table of this snapshot."""
        return [row.to_dict() for row in self.verify_content()]

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical snapshot view (paths stay relative)."""
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_schema": self.snapshot_schema,
            "snapshot_schema_version": self.snapshot_schema_version,
            "generation": self.generation,
            "read_root": str(self.read_root),
            "scientific_identity": self.science_identity.to_dict(),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "platform": dict(self.platform),
        }

    def to_json(self) -> str:
        """Return the canonical JSON text for this snapshot."""
        return canonical_json(self.to_dict())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ArtifactSnapshot:
        """Parse a snapshot, resolving every artifact below ``read_root``.

        Content digests are verified by :meth:`verify_content`; this method
        performs structural, role and scientific-identity checks only.
        """
        data = _as_mapping(payload, "snapshot")
        _reject_unknown(data, _SNAPSHOT_KEYS, "snapshot")
        for required in (
            "snapshot_id",
            "snapshot_schema",
            "snapshot_schema_version",
            "generation",
            "read_root",
            "scientific_identity",
            "artifacts",
        ):
            if required not in data:
                raise ArtifactStructuralMismatchError(
                    f"snapshot: missing required field {required!r}"
                )

        schema = _as_string(data["snapshot_schema"], "snapshot.snapshot_schema")
        version = _as_int(data["snapshot_schema_version"], "snapshot.snapshot_schema_version")
        if version != SNAPSHOT_SCHEMA_VERSION:
            raise UnsupportedSnapshotSchemaVersionError(
                f"unsupported snapshot schema_version {version!r}; "
                f"only {SNAPSHOT_SCHEMA_VERSION} is readable"
            )
        generation = data["generation"]
        if isinstance(generation, bool) or not isinstance(generation, (str, int)):
            raise ArtifactStructuralMismatchError(
                "snapshot.generation must be a string or integer provenance token"
            )

        raw_root = _as_string(data["read_root"], "snapshot.read_root")
        read_root = Path(raw_root).expanduser().resolve()

        platform_raw = data.get("platform", {})
        platform = _as_mapping(platform_raw, "snapshot.platform")

        identity = ArtifactScienceIdentity.from_dict(
            _as_mapping(data["scientific_identity"], "snapshot.scientific_identity"),
            "snapshot.scientific_identity",
        )

        entries = _as_sequence(data["artifacts"], "snapshot.artifacts")
        if not entries:
            raise ArtifactStructuralMismatchError("snapshot.artifacts must not be empty")

        artifacts: list[ArtifactRef] = []
        seen_paths: set[str] = set()
        for index, entry in enumerate(entries):
            where = f"snapshot.artifacts[{index}]"
            artifact = _artifact_from_dict(entry, read_root, where)
            if artifact.relative_path in seen_paths:
                raise ArtifactStructuralMismatchError(
                    f"{where}: duplicate artifact path {artifact.relative_path!r}"
                )
            seen_paths.add(artifact.relative_path)
            artifacts.append(artifact)

        structure_digests = {
            artifact.declared_sha256 for artifact in artifacts if artifact.role in STRUCTURE_ROLES
        }
        if not structure_digests:
            raise ArtifactStructuralMismatchError(
                "snapshot.artifacts declares no structure-bearing artifact; "
                f"one of {sorted(STRUCTURE_ROLES)} is required"
            )
        if identity.structure_sha256 not in structure_digests:
            raise ArtifactScienceIdentityError(
                "scientific_identity.structure_sha256 does not match any declared "
                "structure artifact digest"
            )

        return cls(
            snapshot_id=_as_string(data["snapshot_id"], "snapshot.snapshot_id"),
            snapshot_schema=schema,
            snapshot_schema_version=version,
            generation=generation,
            read_root=read_root,
            science_identity=identity,
            artifacts=tuple(sorted(artifacts, key=lambda item: (item.role, item.relative_path))),
            platform=platform,
        )


def _artifact_from_dict(entry: Any, read_root: Path, where: str) -> ArtifactRef:
    data = _as_mapping(entry, where)
    _reject_unknown(data, _ARTIFACT_KEYS, where)
    for required in ("role", "path", "sha256"):
        if required not in data:
            raise ArtifactStructuralMismatchError(f"{where}: missing required field {required!r}")
    role = _as_string(data["role"], f"{where}.role")
    relative_path, resolved = _resolve_artifact_path(read_root, data["path"], f"{where}.path")
    size = data.get("size_bytes")
    media_type = data.get("media_type")
    if media_type is not None:
        media_type = _as_string(media_type, f"{where}.media_type")
    return ArtifactRef(
        role=role,
        relative_path=relative_path,
        declared_sha256=_normalise_digest(data["sha256"], f"{where}.sha256"),
        resolved_path=resolved,
        size_bytes=size,
        media_type=media_type,
    )


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_verified_snapshot(
    payload: Mapping[str, Any],
) -> tuple[ArtifactSnapshot, tuple[ArtifactDigest, ...]]:
    """Parse a snapshot and verify every artifact digest against its bytes."""
    snapshot = ArtifactSnapshot.from_dict(payload)
    return snapshot, snapshot.verify_content()
