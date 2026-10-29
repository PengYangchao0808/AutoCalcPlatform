"""Production enablement of installed third-party science plugins (M2-05).

The platform already had every *part* of a plugin pipeline and no production
assembly: CCCP discovers installed ``cccp.science_plugins`` entry points
metadata-only (:mod:`cccp.plugin.discovery`), loads them only after an explicit
enablement (:mod:`cccp.plugin.host`) and invokes a declared capability through
:func:`cccp.plugin.sdk.invoke_capability`; the platform kept server-side
authorization and frozen snapshots in
:mod:`acp_services.runtime.capability_registry`.  Nothing joined the two, so a
third-party capability could never appear in a real service's discovery answer.

This module is that join, and it deliberately stays small:

1. **Installed fact** — scan the entry-point group metadata-only and compute the
   distribution digest from the bytes that are *actually installed*.
   ``cccp_science_plugin.json`` may claim a ``distribution_digest``; the claim is
   only ever compared with the computed value, never used in its place.
2. **Enablement ledger** — persist ``installed/enabled/enabled_by/enabled_at``
   (plus the descriptor digest and the computed distribution digest) under
   ``<state_root>/plugins/<plugin>/<version>/enablement.json``.  A new process
   reads the installed fact from the environment and the enabled fact from this
   ledger; no in-process registration is required.
3. **Authorization** — every enable/disable needs a server-side
   :class:`~acp_services.runtime.capability_registry.CapabilityAuthorization`
   fact bound to the *computed* descriptor digest, and the capability (or its
   plugin) must be on the explicit trust allow-list.  Untrusted, unlicensed,
   version-incompatible and digest-mismatching installations fail closed.
4. **Read-only history** — enabling writes a write-once descriptor snapshot
   through :func:`acp_services.runtime.capability_registry.write_read_only_snapshot`,
   so an upgrade or uninstall never makes a historical record unreadable and a
   reader never resolves the currently active version.

Scope boundary (frozen by M2-05): only ``pure_analysis`` capabilities are
enabled here.  A third-party **compute** capability stays a DEFERRED row — it
would have to enter a worker attempt with a frozen group member, and this
module refuses to enable one instead of pretending the execution station moved.
Discovery itself never imports the plugin module; only the enabled capability's
invocation (in the composition root) does.

The module imports no :mod:`acp` code and starts no Controller, worker, subprocess
or task.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from acp_services.runtime.capability_registry import (
    CapabilityAuthorization,
    CapabilityConflictError,
    CapabilityError,
    CapabilityUntrustedError,
    CapabilityVersionRejectedError,
    require_capability_authorization,
    write_read_only_snapshot,
)
from cccp.calculation.capabilities.contracts import CapabilityKind
from cccp.plugin.discovery import discover_plugins, scan_science_plugin_entry_points
from cccp.plugin.metadata import PluginLicensePolicy, PluginMetadata

logger = logging.getLogger(__name__)

__all__ = [
    "DISTRIBUTION_DIGEST_ALGORITHM",
    "PLUGIN_DESCRIPTOR_SCHEMA",
    "PLUGIN_ENABLEMENT_SCHEMA",
    "SCIENCE_PLUGIN_STATE_DIR",
    "DistributionDigest",
    "InstalledCapability",
    "PluginEnablement",
    "SciencePluginRegistry",
    "compute_distribution_digest",
]

#: Algorithm id of the computed distribution digest (declared in the record).
DISTRIBUTION_DIGEST_ALGORITHM = "acp2.plugin.distribution-digest.v1"
#: Persisted enablement document schema.
PLUGIN_ENABLEMENT_SCHEMA = "acp2.plugin.enablement.v1"
#: Projected capability descriptor schema (what discovery forwards).
PLUGIN_DESCRIPTOR_SCHEMA = "acp2.plugin.capability-descriptor.v1"
#: Directory (under the platform state root) holding the ledger and snapshots.
SCIENCE_PLUGIN_STATE_DIR = "plugins"
#: Reserved per-version snapshot directory.  The frozen snapshot keeps a copy of
#: the enablement record for history; it is never a live ledger entry.
SNAPSHOT_DIR_NAME = "snapshot"

#: Powers the descriptor/form digests; a declared algorithm id, not a silent detail.
DESCRIPTOR_DIGEST_PREFIX = "sha256:"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SEPARATOR = b"\x00"
#: Distribution metadata/relocation trees are never part of the payload digest.
_NON_PAYLOAD_TOP_LEVEL_SUFFIXES = (".dist-info", ".data", ".egg-info")


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def _digest_document(document: Mapping[str, Any]) -> str:
    """Return ``sha256:`` over the canonical sorted JSON bytes of *document*."""
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return DESCRIPTOR_DIGEST_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return DESCRIPTOR_DIGEST_PREFIX + hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class DistributionDigest:
    """The computed distribution identity plus the manifest's claim.

    Attributes:
        algorithm: Digest algorithm id (``acp2.plugin.distribution-digest.v1``).
        digest: ``sha256:`` over the *installed* payload bytes.
        declared: The manifest's own ``distribution_digest`` claim (may be absent).
        file_count: Number of payload files hashed.
        matches_declared: ``declared == digest``; the only state that may be
            enabled.  A missing claim is never treated as a match.
    """

    algorithm: str
    digest: str
    declared: str | None
    file_count: int
    matches_declared: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithm": self.algorithm,
            "digest": self.digest,
            "declared": self.declared,
            "file_count": self.file_count,
            "matches_declared": self.matches_declared,
        }


def _payload_entries(distribution: importlib.metadata.Distribution) -> tuple[tuple[str, Path], ...]:
    """Return ``(installed relative path, filesystem path)`` for the payload.

    Metadata directories, relocated ``.data`` trees, paths outside the
    installation and byte-code caches are excluded: they are not what the plugin
    ships as its payload, and the metadata tree contains the manifest that
    carries the claim itself (a digest cannot cover its own inscription).
    """
    files = distribution.files
    if files is None:
        raise CapabilityError(
            f"distribution {distribution.metadata['Name']!r} records no installed files; "
            "its bytes cannot be verified",
            code="capability_distribution_unavailable",
        )
    entries: list[tuple[str, Path]] = []
    for item in files:
        relative = PurePosixPath(str(item).replace(os.sep, "/"))
        parts = relative.parts
        if not parts or ".." in parts:
            continue
        if parts[0].endswith(_NON_PAYLOAD_TOP_LEVEL_SUFFIXES):
            continue
        if "__pycache__" in parts or relative.suffix == ".pyc":
            continue
        entries.append((relative.as_posix(), Path(distribution.locate_file(item))))
    entries.sort(key=lambda entry: entry[0])
    return tuple(entries)


def compute_distribution_digest(
    distribution: importlib.metadata.Distribution,
    *,
    declared: str | None = None,
) -> DistributionDigest:
    """Hash the bytes actually installed for *distribution*.

    The record is built from the installed file list (``RECORD``), never from
    the manifest's own claim: a plugin that edits its manifest after the wheel
    was built cannot make the computed value agree with the edited claim.
    """
    entries = _payload_entries(distribution)
    hasher = hashlib.sha256()
    for relative, path in entries:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise CapabilityError(
                f"installed payload file {relative!r} is unreadable: {exc}",
                code="capability_distribution_unavailable",
            ) from exc
        hasher.update(relative.encode("utf-8"))
        hasher.update(_SEPARATOR)
        hasher.update(payload)
        hasher.update(_SEPARATOR)
    digest = DESCRIPTOR_DIGEST_PREFIX + hasher.hexdigest()
    return DistributionDigest(
        algorithm=DISTRIBUTION_DIGEST_ALGORITHM,
        digest=digest,
        declared=declared,
        file_count=len(entries),
        matches_declared=bool(declared) and declared == digest,
    )


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstalledCapability:
    """One capability of one installed plugin distribution (metadata only)."""

    plugin_name: str
    version: str
    distribution: str
    capability_id: str
    capability_kind: str
    schema: str
    schema_version: int
    implementation_id: str
    license: str
    trust_level: str
    entry_point: str | None
    descriptor_digest: str
    descriptor: Mapping[str, Any]
    distribution_digest: DistributionDigest

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_id, self.version)

    @property
    def is_pure_analysis(self) -> bool:
        return self.capability_kind == CapabilityKind.PURE_ANALYSIS.value

    def to_metadata(self) -> dict[str, Any]:
        """Return the projection a discovery answer may carry (no plugin import)."""
        return {
            "capability_id": self.capability_id,
            "plugin_name": self.plugin_name,
            "version": self.version,
            "distribution": self.distribution,
            "capability_kind": self.capability_kind,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "implementation_id": self.implementation_id,
            "license": self.license,
            "trust_level": self.trust_level,
            "entry_point": self.entry_point,
            "descriptor_digest": self.descriptor_digest,
            "distribution_digest": self.distribution_digest.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PluginEnablement:
    """The persisted enablement fact for one plugin capability.

    Attributes:
        capability_id: Capability name declared by the plugin manifest.
        plugin_name: Distribution plugin name.
        version: Plugin version the enablement is bound to.
        installed: Whether the distribution was installed when enabling.
        installed_at: Recorded installation timestamp (scan time).
        enabled: Whether the operator enabled it.
        enabled_by: Authorizing principal (server-side fact).
        enabled_at: Enablement timestamp.
        disabled_by/disabled_at: Last disablement (kept for history).
        descriptor_digest: Digest of the frozen descriptor document.
        distribution_digest: Frozen computed distribution digest record.
        descriptor: The frozen descriptor document.
    """

    capability_id: str
    plugin_name: str
    version: str
    installed: bool
    installed_at: str
    enabled: bool
    enabled_by: str | None
    enabled_at: str | None
    descriptor_digest: str
    distribution_digest: Mapping[str, Any]
    descriptor: Mapping[str, Any]
    schema: str = PLUGIN_ENABLEMENT_SCHEMA
    disabled_by: str | None = None
    disabled_at: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_id, self.version)

    @property
    def computed_distribution_digest(self) -> str:
        return str(self.distribution_digest.get("digest") or "")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "plugin_name": self.plugin_name,
            "version": self.version,
            "installed": self.installed,
            "installed_at": self.installed_at,
            "enabled": self.enabled,
            "enabled_by": self.enabled_by,
            "enabled_at": self.enabled_at,
            "disabled_by": self.disabled_by,
            "disabled_at": self.disabled_at,
            "descriptor_digest": self.descriptor_digest,
            "distribution_digest": dict(self.distribution_digest),
            "descriptor": dict(self.descriptor),
        }

    @classmethod
    def from_record(cls, document: Mapping[str, Any]) -> PluginEnablement:
        if document.get("schema") != PLUGIN_ENABLEMENT_SCHEMA:
            raise CapabilityError(
                f"unknown plugin enablement schema {document.get('schema')!r}",
                code="capability_enablement_invalid",
            )
        digest = document.get("distribution_digest")
        if not isinstance(digest, Mapping):
            raise CapabilityError(
                "plugin enablement record carries no distribution digest",
                code="capability_enablement_invalid",
            )
        descriptor = document.get("descriptor")
        if not isinstance(descriptor, Mapping):
            raise CapabilityError(
                "plugin enablement record carries no descriptor",
                code="capability_enablement_invalid",
            )
        return cls(
            capability_id=str(document["capability_id"]),
            plugin_name=str(document["plugin_name"]),
            version=str(document["version"]),
            installed=bool(document.get("installed")),
            installed_at=str(document.get("installed_at", "")),
            enabled=bool(document.get("enabled")),
            enabled_by=(str(document["enabled_by"]) if document.get("enabled_by") else None),
            enabled_at=(str(document["enabled_at"]) if document.get("enabled_at") else None),
            descriptor_digest=str(document.get("descriptor_digest", "")),
            distribution_digest=dict(digest),
            descriptor=dict(descriptor),
            disabled_by=(str(document["disabled_by"]) if document.get("disabled_by") else None),
            disabled_at=(str(document["disabled_at"]) if document.get("disabled_at") else None),
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class SciencePluginRegistry:
    """Installed-plugin enablement ledger and authorized descriptor projection.

    Args:
        state_root: Platform state root; the ledger lives under ``plugins/``.
        trusted_capabilities: Explicit trust allow-list of capability ids.
        trusted_plugins: Explicit trust allow-list of plugin names.
        authorized_principals: Principals allowed to issue authorization facts.
        entry_points: Injected entry points (tests); ``None`` scans importlib.
        manifest_reader: Injected manifest reader (tests).
        license_policy: Explicit accepted-license policy.
        distribution_reader: Injected installed-distribution reader. The default
            reads :mod:`importlib.metadata`; tests may inject a fake installed
            distribution to exercise the fail-closed digest path.
        now: Clock returning an ISO-8601 UTC timestamp.
    """

    def __init__(
        self,
        *,
        state_root: str | Path,
        trusted_capabilities: Sequence[str] = (),
        trusted_plugins: Sequence[str] = (),
        authorized_principals: Sequence[str] = ("controller",),
        entry_points: object = None,
        manifest_reader: Callable[[object], str | None] | None = None,
        license_policy: PluginLicensePolicy | None = None,
        distribution_reader: Callable[[str], importlib.metadata.Distribution] | None = None,
        now: Callable[[], str] | None = None,
    ) -> None:
        self.state_root = Path(state_root)
        self.plugins_root = self.state_root / SCIENCE_PLUGIN_STATE_DIR
        self.trusted_capabilities = frozenset(trusted_capabilities)
        self.trusted_plugins = frozenset(trusted_plugins)
        self.authorized_principals = frozenset(authorized_principals)
        self._entry_points = entry_points
        self._manifest_reader = manifest_reader
        self._license_policy = license_policy
        self._distribution_reader = distribution_reader
        self._now = now or _utcnow
        self._installed: dict[tuple[str, str], InstalledCapability] = {}
        self._installed_at: str = ""
        self._scanned = False
        self._refusals: tuple[dict[str, Any], ...] = ()

    # -- installed fact --------------------------------------------------

    def scan(self, *, refresh: bool = True) -> tuple[InstalledCapability, ...]:
        """Discover installed plugins metadata-only and verify their bytes.

        Raises:
            PluginMetadataError/PluginLicenseError/PluginVersionMismatchError/
            PluginNameConflictError/PluginCapabilityConflictError: an installed
            distribution violates the plugin contract (fail closed, before any
            module import).
            CapabilityError: the installed payload is unreadable.
        """
        if self._scanned and not refresh:
            return tuple(sorted(self._installed.values(), key=lambda item: item.key))
        scanned_at = self._now()
        entry_points = self._entry_points
        if entry_points is None:
            entry_points = importlib.metadata.entry_points()
        scan = scan_science_plugin_entry_points(
            entry_points=entry_points,
            manifest_reader=self._manifest_reader,
            license_policy=self._license_policy,
        )
        catalog = discover_plugins(scan.candidates)
        origins = {origin.name: origin for origin in catalog.origins}
        installed: dict[tuple[str, str], InstalledCapability] = {}
        for metadata in catalog.plugins:
            # Metadata only: ``scan.candidates`` loaders are stored, never called.
            digest = self._distribution_digest(metadata)
            origin = origins.get(metadata.name)
            for capability in metadata.capabilities:
                descriptor = self._descriptor_document(metadata, capability, digest)
                record = InstalledCapability(
                    plugin_name=metadata.name,
                    version=metadata.version,
                    distribution=metadata.distribution,
                    capability_id=capability.name,
                    capability_kind=capability.kind.value,
                    schema=capability.schema,
                    schema_version=int(capability.schema_version),
                    implementation_id=capability.implementation_id,
                    license=metadata.license,
                    trust_level=metadata.trust_level.value,
                    entry_point=(origin.entry_point if origin is not None else None),
                    descriptor_digest=_digest_document(descriptor),
                    descriptor=descriptor,
                    distribution_digest=digest,
                )
                installed[record.key] = record
            logger.debug(
                "science plugin %s@%s installed; computed digest %s (declared %s, match=%s)",
                metadata.name,
                metadata.version,
                digest.digest,
                digest.declared,
                digest.matches_declared,
            )
        self._installed = installed
        self._installed_at = scanned_at
        self._scanned = True
        self._refresh_refusals()
        return tuple(sorted(installed.values(), key=lambda item: item.key))

    def installed_capabilities(self) -> tuple[InstalledCapability, ...]:
        """Return the installed capability set (scanning once on first use)."""
        return self.scan(refresh=False)

    def installed_capability(
        self, capability_id: str, version: str | None = None
    ) -> InstalledCapability:
        """Return one installed capability or raise a typed refusal."""
        matches = [
            record
            for record in self.installed_capabilities()
            if record.capability_id == capability_id
            and (version is None or record.version == version)
        ]
        if not matches:
            raise CapabilityVersionRejectedError(
                f"{capability_id} is not installed in this environment",
                code="capability_not_installed",
            )
        if len(matches) > 1:
            raise CapabilityVersionRejectedError(
                f"{capability_id} is installed in several versions; name one explicitly",
                code="capability_version_ambiguous",
            )
        return matches[0]

    # -- enablement ------------------------------------------------------

    def enable(
        self,
        capability_id: str,
        *,
        authorization: CapabilityAuthorization | None,
        version: str | None = None,
    ) -> PluginEnablement:
        """Enable one installed pure-analysis capability (persisted)."""
        record = self.installed_capability(capability_id, version)
        self._require_trust(record)
        self._require_verified_digest(record)
        if not record.is_pure_analysis:
            raise CapabilityError(
                f"{capability_id} is a {record.capability_kind!r} capability; a "
                "worker-hosted third-party COMPUTE capability stays DEFERRED (M2-05) and "
                "is never enabled by the pure-analysis pipeline",
                code="capability_worker_hosted_deferred",
            )
        require_capability_authorization(
            authorization,
            action="capability.enable",
            capability_id=record.capability_id,
            version=record.version,
            digest=record.descriptor_digest,
            authorized_principals=self.authorized_principals,
            now=self._now(),
        )
        enabled_by = str(authorization.authorized_by)  # type: ignore[union-attr]
        now = self._now()
        existing = self._read_enablement(record.plugin_name, record.version, record.capability_id)
        enablement = PluginEnablement(
            capability_id=record.capability_id,
            plugin_name=record.plugin_name,
            version=record.version,
            installed=True,
            installed_at=self._installed_at or now,
            enabled=True,
            enabled_by=enabled_by,
            enabled_at=now,
            descriptor_digest=record.descriptor_digest,
            distribution_digest=record.distribution_digest.to_dict(),
            descriptor=dict(record.descriptor),
            disabled_by=(existing.disabled_by if existing is not None else None),
            disabled_at=(existing.disabled_at if existing is not None else None),
        )
        self._write_enablement(enablement)
        self._write_snapshot(enablement)
        self._bump_generation()
        self._refresh_refusals()
        logger.info(
            "enabled science plugin capability %s@%s by %s (distribution %s)",
            enablement.capability_id,
            enablement.version,
            enabled_by,
            enablement.computed_distribution_digest,
        )
        return enablement

    def disable(
        self,
        capability_id: str,
        *,
        authorization: CapabilityAuthorization | None,
        version: str | None = None,
    ) -> PluginEnablement:
        """Disable one enabled capability; the frozen snapshot stays readable."""
        existing = self._find_enablement(capability_id, version)
        if existing is None:
            raise CapabilityVersionRejectedError(
                f"{capability_id} is not enabled",
                code="capability_not_enabled",
            )
        # The authorization is bound to the *frozen* descriptor digest: an
        # operator disables exactly what was enabled, never a later edit.
        require_capability_authorization(
            authorization,
            action="capability.disable",
            capability_id=existing.capability_id,
            version=existing.version,
            digest=existing.descriptor_digest,
            authorized_principals=self.authorized_principals,
            now=self._now(),
        )
        updated = _replace_enablement(
            existing,
            enabled=False,
            disabled_by=str(authorization.authorized_by),  # type: ignore[union-attr]
            disabled_at=self._now(),
        )
        self._write_enablement(updated)
        self._bump_generation()
        self._refresh_refusals()
        logger.info("disabled science plugin capability %s@%s", capability_id, existing.version)
        return updated

    # -- ledger reads ----------------------------------------------------

    def enablements(self) -> tuple[PluginEnablement, ...]:
        """Return every persisted enablement record (enabled and disabled)."""
        records: dict[tuple[str, str], PluginEnablement] = {}
        for path in self._ledger_paths():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                record = PluginEnablement.from_record(document)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, CapabilityError):
                logger.warning("ignoring unreadable science plugin enablement %s", path)
                continue
            records[record.key] = record
        return tuple(sorted(records.values(), key=lambda item: item.key))

    def enablement(self, capability_id: str, version: str | None = None) -> PluginEnablement | None:
        """Return one persisted enablement record (``None`` when absent)."""
        return self._find_enablement(capability_id, version)

    def authorized_capabilities(self) -> tuple[PluginEnablement, ...]:
        """Return enabled+installed+byte-verified capabilities (executable now).

        A capability whose installed payload no longer matches the frozen
        distribution digest is refused here (never silently executed) and shows
        up in :meth:`refusals`.
        """
        self.installed_capabilities()
        authorized: list[PluginEnablement] = []
        for record in self.enablements():
            if not record.enabled:
                continue
            current = self._installed.get(record.key)
            if current is None:
                continue
            if current.distribution_digest.digest != record.computed_distribution_digest:
                continue
            if not current.distribution_digest.matches_declared:
                continue
            authorized.append(record)
        return tuple(authorized)

    def refusals(self) -> tuple[dict[str, Any], ...]:
        """Return why enabled/installed capabilities are not executable now."""
        return self._refusals

    def generation(self) -> int:
        """Return the enablement generation (cheap observable for cache keys)."""
        return self._read_generation()

    def ledger_signature(self) -> str:
        """Return a cheap signature of the ledger file set (name/size/mtime)."""
        parts: list[str] = []
        for path in self._ledger_paths():
            try:
                stat = path.stat()
            except OSError:
                continue
            parts.append(
                f"{path.relative_to(self.plugins_root).as_posix()}:{stat.st_size}:{stat.st_mtime_ns}"
            )
        return "|".join(parts) or "empty"

    def frozen_descriptor(self, plugin_name: str, version: str) -> Mapping[str, Any]:
        """Return the read-only descriptor snapshot (never the active version).

        The snapshot document is the projected capability descriptor annotated
        with the frozen identity a historical reader needs (its own
        ``descriptor_digest`` plus the computed ``distribution_digest``) — the
        same annotations the discovery projection carries — and it never
        resolves whatever version happens to be installed now.
        """
        path = self._snapshot_descriptor_path(plugin_name, version)
        if not path.is_file():
            raise CapabilityVersionRejectedError(
                f"no frozen descriptor snapshot for {plugin_name}@{version}",
                code="capability_artifact_unavailable",
            )
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CapabilityError(
                f"frozen descriptor snapshot is unreadable: {exc}",
                code="capability_artifact_unavailable",
            ) from exc
        if not isinstance(document, dict):
            raise CapabilityError(
                "frozen descriptor snapshot is not an object",
                code="capability_artifact_invalid",
            )
        return document

    # -- internals -------------------------------------------------------

    def _distribution_digest(self, metadata: PluginMetadata) -> DistributionDigest:
        distribution = self._read_distribution(metadata.distribution)
        return compute_distribution_digest(
            distribution,
            declared=metadata.distribution_digest,
        )

    def _read_distribution(self, name: str) -> importlib.metadata.Distribution:
        if self._distribution_reader is not None:
            return self._distribution_reader(name)
        try:
            return importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise CapabilityVersionRejectedError(
                f"distribution {name!r} is not installed; only its metadata claim exists",
                code="capability_not_installed",
            ) from exc

    def _descriptor_document(
        self,
        metadata: PluginMetadata,
        capability: Any,
        digest: DistributionDigest,
    ) -> dict[str, Any]:
        """Project one plugin capability as an A-compatible descriptor document.

        The document is platform-owned (``schema`` names the plugin descriptor)
        and carries the provenance a reader needs without importing the plugin:
        plugin identity, capability schema and the computed distribution digest.
        """
        return {
            "schema": PLUGIN_DESCRIPTOR_SCHEMA,
            "schema_version": 1,
            "capability_id": capability.name,
            "implementation_id": capability.implementation_id,
            "capability_source": "science_plugin",
            "plugin": {
                "name": metadata.name,
                "version": metadata.version,
                "distribution": metadata.distribution,
                "license": metadata.license,
                "trust_level": metadata.trust_level.value,
                "entry_point_group": metadata.entry_point_group_version,
            },
            "input_schema": {
                "ref": "acp2.science.prepared-artifact.v1",
                "schema_version": 1,
            },
            "output_schema": {
                "ref": capability.schema,
                "schema_version": int(capability.schema_version),
            },
            "execution_semantics": {
                "kind": capability.kind.value,
                "starts_subprocess": bool(metadata.resources.requires_subprocess),
                "network_access": bool(metadata.resources.requires_network),
                "delegates_to_task_kind": capability.delegates_to_task_kind,
            },
            "provenance": {
                "descriptor_digest_algorithm": "acp2.plugin.descriptor-digest.v1",
                "distribution_digest": digest.to_dict(),
                "fixture_evidence": [item.to_dict() for item in metadata.fixture_evidence],
                "validator_refs": [item.to_dict() for item in capability.validator_refs],
                "recipe_refs": [item.to_dict() for item in capability.recipe_refs],
            },
        }

    def _require_trust(self, record: InstalledCapability) -> None:
        trusted = (
            record.capability_id in self.trusted_capabilities
            or record.plugin_name in self.trusted_plugins
        )
        if not trusted:
            raise CapabilityUntrustedError(
                f"{record.capability_id} is discover-only; add the capability or its "
                "plugin to the explicit trust allow-list to enable it",
                code="capability_untrusted",
            )

    def _require_verified_digest(self, record: InstalledCapability) -> None:
        digest = record.distribution_digest
        if digest.declared is None:
            raise CapabilityVersionRejectedError(
                f"{record.plugin_name}@{record.version} declares no distribution digest; "
                "the installed bytes cannot be verified",
                code="capability_distribution_digest_missing",
            )
        if not digest.matches_declared:
            raise CapabilityConflictError(
                f"{record.plugin_name}@{record.version} declares distribution digest "
                f"{digest.declared} but the installed bytes hash to {digest.digest}",
                code="capability_distribution_digest_mismatch",
            )

    def _refresh_refusals(self) -> None:
        refusals: list[dict[str, Any]] = []
        for record in self.enablements():
            if not record.enabled:
                continue
            current = self._installed.get(record.key)
            if current is None:
                refusals.append(
                    {
                        "capability_id": record.capability_id,
                        "version": record.version,
                        "reason": "plugin_not_installed",
                    }
                )
                continue
            if not current.distribution_digest.matches_declared:
                refusals.append(
                    {
                        "capability_id": record.capability_id,
                        "version": record.version,
                        "reason": "distribution_digest_mismatch",
                        "declared": current.distribution_digest.declared,
                        "computed": current.distribution_digest.digest,
                    }
                )
                continue
            if current.distribution_digest.digest != record.computed_distribution_digest:
                refusals.append(
                    {
                        "capability_id": record.capability_id,
                        "version": record.version,
                        "reason": "installed_bytes_changed_since_enablement",
                        "enabled_digest": record.computed_distribution_digest,
                        "computed": current.distribution_digest.digest,
                    }
                )
        self._refusals = tuple(refusals)

    def _find_enablement(self, capability_id: str, version: str | None) -> PluginEnablement | None:
        for record in self.enablements():
            if record.capability_id == capability_id and (
                version is None or record.version == version
            ):
                return record
        return None

    def _read_enablement(
        self,
        plugin_name: str,
        version: str,
        capability_id: str,
    ) -> PluginEnablement | None:
        path = self._enablement_path(plugin_name, version, capability_id)
        if not path.is_file():
            return None
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            return PluginEnablement.from_record(document)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, CapabilityError):
            logger.warning("ignoring unreadable science plugin enablement %s", path)
            return None

    def _enablement_dir(self, plugin_name: str, version: str, capability_id: str) -> Path:
        return (
            self.plugins_root
            / _SAFE_NAME.sub("_", plugin_name)
            / _SAFE_NAME.sub("_", version)
            / _SAFE_NAME.sub("_", capability_id)
        )

    def _enablement_path(self, plugin_name: str, version: str, capability_id: str) -> Path:
        return self._enablement_dir(plugin_name, version, capability_id) / "enablement.json"

    def _snapshot_descriptor_path(self, plugin_name: str, version: str) -> Path:
        return (
            self.plugins_root
            / _SAFE_NAME.sub("_", plugin_name)
            / _SAFE_NAME.sub("_", version)
            / SNAPSHOT_DIR_NAME
            / "descriptor.json"
        )

    def _ledger_paths(self) -> tuple[Path, ...]:
        """Return the live enablement records (never the frozen snapshot copy).

        The frozen snapshot directory keeps a historical copy of the
        enablement document, including ``enabled: true`` at the time it was
        frozen.  It is history, not a live fact: reading it back as a ledger
        entry would resurrect a disabled capability.
        """
        if not self.plugins_root.is_dir():
            return ()
        return tuple(
            path
            for path in sorted(self.plugins_root.glob("*/*/*/enablement.json"))
            if path.parent.name != SNAPSHOT_DIR_NAME
        )

    def _write_enablement(self, enablement: PluginEnablement) -> None:
        path = self._enablement_path(
            enablement.plugin_name, enablement.version, enablement.capability_id
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(enablement.to_record(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _write_snapshot(self, enablement: PluginEnablement) -> None:
        """Write the read-only descriptor snapshot once (never overwritten)."""
        directory = self._snapshot_descriptor_path(
            enablement.plugin_name, enablement.version
        ).parent
        if (directory / "descriptor.json").is_file():
            # Write-once: an upgrade is a new version directory, and a re-enable
            # of the same version never rewrites the historical record.
            return
        write_read_only_snapshot(
            directory,
            {
                "descriptor": _frozen_descriptor_document(enablement),
                "enablement": enablement.to_record(),
                "snapshot": {
                    "schema": "acp2.plugin.capability-snapshot.v1",
                    "plugin_name": enablement.plugin_name,
                    "version": enablement.version,
                    "capability_id": enablement.capability_id,
                    "descriptor_digest": enablement.descriptor_digest,
                    "distribution_digest": dict(enablement.distribution_digest),
                    "enabled_at": enablement.enabled_at,
                    "enabled_by": enablement.enabled_by,
                    "read_only": True,
                },
            },
        )

    def _generation_path(self) -> Path:
        return self.plugins_root / "generation.json"

    def _read_generation(self) -> int:
        path = self._generation_path()
        if not path.is_file():
            return 0
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        value = document.get("generation") if isinstance(document, Mapping) else None
        return int(value) if isinstance(value, int) else 0

    def _bump_generation(self) -> int:
        generation = self._read_generation() + 1
        path = self._generation_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps({"schema": "acp2.plugin.generation.v1", "generation": generation}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return generation


def _frozen_descriptor_document(enablement: PluginEnablement) -> dict[str, Any]:
    """Return the descriptor document annotated with its frozen identity.

    The annotations are the same platform-owned facts the discovery projection
    carries (``descriptor_digest`` + computed ``distribution_digest``).  They
    are added *after* the descriptor digest is computed at scan time, so the
    digest still covers exactly the projected descriptor and never itself.
    """
    document = dict(enablement.descriptor)
    document["descriptor_digest"] = enablement.descriptor_digest
    document["distribution_digest"] = dict(enablement.distribution_digest)
    document["read_only"] = True
    return document


def _replace_enablement(record: PluginEnablement, **changes: Any) -> PluginEnablement:
    payload = record.to_record()
    payload.pop("schema", None)
    payload.update(changes)
    return PluginEnablement.from_record({**payload, "schema": PLUGIN_ENABLEMENT_SCHEMA})


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
