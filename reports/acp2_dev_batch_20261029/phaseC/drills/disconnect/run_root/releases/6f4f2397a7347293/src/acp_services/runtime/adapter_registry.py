"""Infrastructure adapter discovery, authorization and per-task freezing (B21).

This is the *only* platform adapter registry authority.  It describes existing
Local/LSF/storage/transfer/identity capabilities and any explicitly trusted
externally installed infrastructure adapter.  It never starts a Controller,
never imports ``acp`` and never executes a declared entrypoint during
discovery/install/enable.

Lifecycle
---------
1. :meth:`AdapterRegistry.discover` scans ``<plugin_root>/*/adapter.json`` and
   reads metadata plus entrypoint bytes.  It does **not** import or execute the
   entrypoint; rejected descriptors are reported, never silently dropped.
2. :meth:`AdapterRegistry.install` needs a server-side
   :class:`AdapterAuthorization` fact, an explicitly trusted adapter id, a
   compatible version, a satisfied environment and non-conflicting capability
   ownership.
3. :meth:`AdapterRegistry.enable` needs a second authorization fact and an
   exact installed version; it never resolves to "latest" and never replaces an
   unknown version.
4. :meth:`AdapterRegistry.freeze_for_task` writes the exact adapter
   id/version/descriptor digest/entrypoint digest into the task attempt.  A
   later version install does not mutate that record; the old task reads its
   frozen adapter back by digest.
5. :meth:`AdapterRegistry.invoke` / :meth:`invoke_frozen` execute only with
   ``allow_execution=True``.  An in-process plugin is **not** a sandbox: the
   registry always reports process/OS isolation as ``NOT_VERIFIED`` unless a
   separate execution environment provides and proves it.

No science parser/threshold is accepted here: the frozen contract in
:mod:`acp_services.contracts.adapters` rejects scientific capability names and
the registry never interprets result semantics.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp_services.contracts.adapters import (
    AdapterContractError,
    AdapterDescriptor,
    AdapterResourceLimits,
    TrustLevel,
    builtin_adapter_descriptors,
    descriptor_digest,
)

logger = logging.getLogger(__name__)

__all__ = [
    "TASK_FREEZE_SCHEMA",
    "TASK_ARTIFACT_SCHEMA",
    "DISCOVERY_SCHEMA",
    "ISOLATION_SCHEMA",
    "PROCESS_ISOLATION_NOT_VERIFIED",
    "AdapterRegistryError",
    "AdapterDiscoveryError",
    "AdapterNotFoundError",
    "AdapterVersionRejectedError",
    "AdapterEnvironmentMissingError",
    "AdapterCapabilityConflictError",
    "AdapterAuthorizationError",
    "AdapterUntrustedError",
    "AdapterInvocationError",
    "AdapterAuthorization",
    "AdapterResourceLimits",
    "DiscoveredAdapter",
    "DiscoveryRejection",
    "DiscoveryReport",
    "InstalledAdapter",
    "FrozenAdapter",
    "TaskAdapterFreeze",
    "FrozenArtifact",
    "AdapterRegistry",
    "default_environment_probe",
]


TASK_FREEZE_SCHEMA = "acp2.adapter.task-freeze.v1"
TASK_ARTIFACT_SCHEMA = "acp2.adapter.frozen-artifact.v1"
DISCOVERY_SCHEMA = "acp2.adapter.discovery.v1"
ISOLATION_SCHEMA = "acp2.adapter.isolation.v1"
PROCESS_ISOLATION_NOT_VERIFIED = "NOT_VERIFIED"
SUPPORTED_VERSION_MAJOR = "1"

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AdapterRegistryError(RuntimeError):
    """Base registry error carrying a stable machine-readable code."""

    code = "adapter_registry_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(f"{code or self.code}: {detail}")
        self.code = code or self.code
        self.detail = detail


class AdapterDiscoveryError(AdapterRegistryError):
    code = "adapter_discovery_error"


class AdapterNotFoundError(AdapterRegistryError):
    code = "adapter_not_found"


class AdapterVersionRejectedError(AdapterRegistryError):
    code = "adapter_version_rejected"


class AdapterEnvironmentMissingError(AdapterRegistryError):
    code = "adapter_environment_missing"

    def __init__(self, adapter_id: str, version: str, missing: Sequence[str]) -> None:
        self.adapter_id = adapter_id
        self.version = version
        self.missing_environment = tuple(missing)
        super().__init__(
            f"missing environment for {adapter_id}@{version}: {', '.join(missing)}",
            code=self.code,
        )


class AdapterCapabilityConflictError(AdapterRegistryError):
    code = "adapter_capability_conflict"

    def __init__(self, capability: str, owner: str, requested: str) -> None:
        self.capability = capability
        self.owner = owner
        self.requested = requested
        super().__init__(
            f"capability {capability!r} is already owned by {owner!r}; "
            f"{requested!r} cannot claim it",
            code=self.code,
        )


class AdapterAuthorizationError(AdapterRegistryError):
    code = "adapter_authorization_required"


class AdapterUntrustedError(AdapterRegistryError):
    code = "adapter_untrusted"


class AdapterInvocationError(AdapterRegistryError):
    code = "adapter_invocation_error"


# ---------------------------------------------------------------------------
# Authorization fact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterAuthorization:
    """A server-side authorization fact for one adapter action.

    This object is constructed by the trusted composition root/controller — it
    is never accepted from plugin metadata or a client request body.  The
    registry checks the action, adapter identity, descriptor digest, principal
    and expiry before installation or enablement.
    """

    action: str
    adapter_id: str
    version: str
    descriptor_digest: str
    authorized_by: str
    issued_at: str
    expires_at: str | None = None

    def is_expired(self, now: str) -> bool:
        return self.expires_at is not None and self.expires_at <= now


# ---------------------------------------------------------------------------
# Discovery / installation records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscoveryRejection:
    path: str
    code: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "detail": self.detail}


@dataclass(frozen=True)
class DiscoveredAdapter:
    descriptor: AdapterDescriptor
    descriptor_path: str
    descriptor_digest: str
    entrypoint_path: str | None
    entrypoint_digest: str | None
    declared_trust_level: str
    effective_trust_level: str
    origin: str = "external"

    @property
    def key(self) -> tuple[str, str]:
        return (self.descriptor.adapter_id, self.descriptor.version)

    def to_metadata(self) -> dict[str, Any]:
        """Metadata only — this method never touches the entrypoint."""

        return {
            "adapter_id": self.descriptor.adapter_id,
            "version": self.descriptor.version,
            "kind": self.descriptor.kind,
            "supported_schema": list(self.descriptor.supported_schema),
            "environment": list(self.descriptor.environment),
            "resource_limits": self.descriptor.resource_limits.to_dict(),
            "verification_fixture": self.descriptor.verification_fixture,
            "capabilities": sorted(self.descriptor.capabilities),
            "declared_trust_level": self.declared_trust_level,
            "effective_trust_level": self.effective_trust_level,
            "descriptor_digest": self.descriptor_digest,
            "entrypoint_digest": self.entrypoint_digest,
            "descriptor_path": self.descriptor_path,
            "entrypoint_path": self.entrypoint_path,
            "origin": self.origin,
            "executed": False,
        }


@dataclass(frozen=True)
class DiscoveryReport:
    plugin_root: str
    scanned_at: str
    adapters: tuple[DiscoveredAdapter, ...]
    rejected: tuple[DiscoveryRejection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": DISCOVERY_SCHEMA,
            "plugin_root": self.plugin_root,
            "scanned_at": self.scanned_at,
            "execution": "none",
            "network": "none",
            "adapters": [adapter.to_metadata() for adapter in self.adapters],
            "rejected": [rejection.to_dict() for rejection in self.rejected],
        }


@dataclass(frozen=True)
class InstalledAdapter:
    descriptor: AdapterDescriptor
    descriptor_digest: str
    entrypoint_path: str | None
    entrypoint_digest: str | None
    installed_at: str
    installed_by: str
    enabled: bool
    effective_trust_level: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.descriptor.adapter_id, self.descriptor.version)

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": "acp2.adapter.installed.v1",
            "descriptor": self.descriptor.to_dict(),
            "descriptor_digest": self.descriptor_digest,
            "entrypoint_path": self.entrypoint_path,
            "entrypoint_digest": self.entrypoint_digest,
            "installed_at": self.installed_at,
            "installed_by": self.installed_by,
            "enabled": self.enabled,
            "effective_trust_level": self.effective_trust_level,
        }

    @classmethod
    def from_record(cls, document: Mapping[str, Any]) -> InstalledAdapter:
        descriptor = AdapterDescriptor.from_dict(document["descriptor"])
        return cls(
            descriptor=descriptor,
            descriptor_digest=str(document["descriptor_digest"]),
            entrypoint_path=(
                str(document["entrypoint_path"]) if document.get("entrypoint_path") else None
            ),
            entrypoint_digest=(
                str(document["entrypoint_digest"]) if document.get("entrypoint_digest") else None
            ),
            installed_at=str(document["installed_at"]),
            installed_by=str(document["installed_by"]),
            enabled=bool(document.get("enabled", False)),
            effective_trust_level=str(
                document.get("effective_trust_level", TrustLevel.DISCOVER_ONLY)
            ),
        )


@dataclass(frozen=True)
class FrozenAdapter:
    adapter_id: str
    version: str
    kind: str
    descriptor_digest: str
    entrypoint_digest: str | None
    supported_schema: tuple[str, ...]
    environment: tuple[str, ...]
    resource_limits: AdapterResourceLimits
    verification_fixture: str
    capabilities: tuple[str, ...]
    trust_level: str
    snapshot_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "version": self.version,
            "kind": self.kind,
            "descriptor_digest": self.descriptor_digest,
            "entrypoint_digest": self.entrypoint_digest,
            "supported_schema": list(self.supported_schema),
            "environment": list(self.environment),
            "resource_limits": self.resource_limits.to_dict(),
            "verification_fixture": self.verification_fixture,
            "capabilities": list(self.capabilities),
            "trust_level": self.trust_level,
            "snapshot_dir": self.snapshot_dir,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> FrozenAdapter:
        return cls(
            adapter_id=str(document["adapter_id"]),
            version=str(document["version"]),
            kind=str(document["kind"]),
            descriptor_digest=str(document["descriptor_digest"]),
            entrypoint_digest=(
                str(document["entrypoint_digest"]) if document.get("entrypoint_digest") else None
            ),
            supported_schema=tuple(str(item) for item in document["supported_schema"]),
            environment=tuple(str(item) for item in document.get("environment", [])),
            resource_limits=AdapterResourceLimits.from_dict(document.get("resource_limits", {})),
            verification_fixture=str(document["verification_fixture"]),
            capabilities=tuple(str(item) for item in document["capabilities"]),
            trust_level=str(document["trust_level"]),
            snapshot_dir=(str(document["snapshot_dir"]) if document.get("snapshot_dir") else None),
        )


@dataclass(frozen=True)
class TaskAdapterFreeze:
    task_id: str
    attempt: int
    frozen_at: str
    adapters: tuple[FrozenAdapter, ...]
    process_isolation: str = PROCESS_ISOLATION_NOT_VERIFIED
    schema: str = TASK_FREEZE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "frozen_at": self.frozen_at,
            "process_isolation": self.process_isolation,
            "adapters": [adapter.to_dict() for adapter in self.adapters],
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> TaskAdapterFreeze:
        if document.get("schema") != TASK_FREEZE_SCHEMA:
            raise AdapterRegistryError(
                f"unknown task freeze schema {document.get('schema')!r}",
                code="task_freeze_schema_invalid",
            )
        task_id = document.get("task_id")
        attempt = document.get("attempt")
        if not isinstance(task_id, str) or not task_id:
            raise AdapterRegistryError("task freeze has no task_id", code="task_freeze_invalid")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise AdapterRegistryError(
                "task freeze attempt must be a positive integer",
                code="task_freeze_invalid",
            )
        raw_adapters = document.get("adapters")
        if not isinstance(raw_adapters, list) or not raw_adapters:
            raise AdapterRegistryError("task freeze has no adapters", code="task_freeze_invalid")
        return cls(
            task_id=task_id,
            attempt=attempt,
            frozen_at=str(document.get("frozen_at", "")),
            adapters=tuple(
                FrozenAdapter.from_dict(item) for item in raw_adapters if isinstance(item, Mapping)
            ),
            process_isolation=str(
                document.get("process_isolation", PROCESS_ISOLATION_NOT_VERIFIED)
            ),
        )

    def adapter(self, adapter_id: str) -> FrozenAdapter:
        for adapter in self.adapters:
            if adapter.adapter_id == adapter_id:
                return adapter
        raise AdapterNotFoundError(
            f"frozen task {self.task_id!r} does not reference {adapter_id!r}"
        )


@dataclass(frozen=True)
class FrozenArtifact:
    """Read-only bytes of one task's frozen adapter.

    This is the historical read path.  It is served from the per-task snapshot
    written at freeze time, so it keeps working after the adapter version is
    uninstalled or its plugin payload is removed.  It never consults the active
    version and never imports the entrypoint.
    """

    adapter_id: str
    version: str
    descriptor_digest: str
    entrypoint_digest: str | None
    descriptor: Mapping[str, Any]
    entrypoint_bytes: bytes | None
    source: str
    snapshot_dir: str | None
    read_only: bool = True
    schema: str = TASK_ARTIFACT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        """Metadata/digest projection; entrypoint bytes are never inlined."""

        return {
            "schema": self.schema,
            "adapter_id": self.adapter_id,
            "version": self.version,
            "descriptor_digest": self.descriptor_digest,
            "entrypoint_digest": self.entrypoint_digest,
            "entrypoint_size_bytes": (
                len(self.entrypoint_bytes) if self.entrypoint_bytes is not None else 0
            ),
            "entrypoint_sha256": (
                hashlib.sha256(self.entrypoint_bytes).hexdigest()
                if self.entrypoint_bytes is not None
                else None
            ),
            "descriptor": dict(self.descriptor),
            "source": self.source,
            "snapshot_dir": self.snapshot_dir,
            "read_only": self.read_only,
        }


# ---------------------------------------------------------------------------
# Environment probing
# ---------------------------------------------------------------------------


def default_environment_probe(requirement: str) -> bool:
    """Return whether one ``env:/path:/command:/platform:/cluster:`` token holds.

    This is a metadata check only.  It never proves isolation; it only answers
    whether a declared prerequisite is present on this host.
    """

    kind, _, value = requirement.partition(":")
    if kind == "env":
        return value in os.environ
    if kind in {"path", "command"}:
        return shutil.which(value) is not None
    if kind == "platform":
        if value in {"any", "*"}:
            return True
        return sys.platform.startswith(value)
    if kind == "cluster":
        return os.environ.get(f"ACP_CLUSTER_{value.upper()}", "").lower() in {
            "1",
            "true",
            "yes",
        }
    return False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AdapterRegistry:
    """Single platform registry for infrastructure adapters.

    Args:
        plugin_root: Directory whose immediate children contain ``adapter.json``
            plus optional entrypoint modules.  ``None`` means no external plugin
            root is configured (built-ins remain available).
        state_root: Optional persistent root for installed records/active
            versions/task freezes.  Without it the registry is in-process only;
            task freezes are still readable for the lifetime of the instance.
        trusted_adapter_ids: Explicit server-side trust allow-list.  A plugin
            not named here is discoverable metadata only and can never be
            installed/enabled.
        authorized_principals: Principals allowed to issue an authorization
            fact.  Defaults to the Controller role.
        environment_probe: Injectable prerequisite probe (tests/environments).
        now: Injectable ISO-8601 UTC clock.
        enforce_environment: When false, environment prerequisites are recorded
            but not enforced (only for metadata-only tooling/tests).
    """

    def __init__(
        self,
        *,
        plugin_root: str | Path | None = None,
        state_root: str | Path | None = None,
        trusted_adapter_ids: Sequence[str] = (),
        authorized_principals: Sequence[str] = ("controller",),
        environment_probe: Callable[[str], bool] | None = None,
        now: Callable[[], str] | None = None,
        enforce_environment: bool = True,
    ) -> None:
        self.plugin_root = Path(plugin_root) if plugin_root is not None else None
        self.state_root = Path(state_root) if state_root is not None else None
        self.trusted_adapter_ids = frozenset(trusted_adapter_ids)
        self.authorized_principals = frozenset(authorized_principals)
        self._environment_probe = environment_probe or default_environment_probe
        self._now = now or _utcnow
        self._enforce_environment = enforce_environment
        self._discovered: dict[tuple[str, str], DiscoveredAdapter] = {}
        self._installed: dict[tuple[str, str], InstalledAdapter] = {}
        self._active: dict[str, str] = {}
        self._last_report: DiscoveryReport | None = None
        self._task_freezes: dict[tuple[str, int], TaskAdapterFreeze] = {}
        self._snapshots: dict[tuple[str, int, str], FrozenArtifact] = {}
        self._register_builtins()
        self._load_state()

    # -- introspection ----------------------------------------------------

    @property
    def last_discovery(self) -> DiscoveryReport | None:
        return self._last_report

    def installed_adapters(self) -> tuple[InstalledAdapter, ...]:
        return tuple(
            sorted(
                self._installed.values(),
                key=lambda item: (item.descriptor.adapter_id, item.descriptor.version),
            )
        )

    def installed(self, adapter_id: str, version: str) -> InstalledAdapter:
        record = self._installed.get((adapter_id, version))
        if record is None:
            raise AdapterVersionRejectedError(
                f"{adapter_id}@{version} is not installed; the registry never "
                "substitutes another version",
                code="adapter_version_not_installed",
            )
        return record

    def active_version(self, adapter_id: str) -> str | None:
        return self._active.get(adapter_id)

    def is_enabled(self, adapter_id: str, version: str | None = None) -> bool:
        try:
            record = self.installed(adapter_id, version or self._active[adapter_id])
        except (AdapterRegistryError, KeyError):
            return False
        return record.enabled and self._active.get(adapter_id) == record.descriptor.version

    def capability_owners(self) -> dict[str, str]:
        owners: dict[str, str] = {}
        for record in self._installed.values():
            for capability in record.descriptor.capabilities:
                owners.setdefault(capability, record.descriptor.adapter_id)
        return owners

    def isolation_report(self) -> dict[str, Any]:
        return {
            "schema": ISOLATION_SCHEMA,
            "execution_model": "in-process",
            "process_isolation": PROCESS_ISOLATION_NOT_VERIFIED,
            "sandbox": False,
            "trusted_plugins_only": True,
            "reason": (
                "an in-process adapter is not a sandbox; process/OS isolation is a "
                "separate capability and is NOT_VERIFIED until an execution "
                "environment measures it"
            ),
        }

    # -- discovery --------------------------------------------------------

    def discover(self) -> DiscoveryReport:
        """Scan adapter metadata without importing/executing any entrypoint."""

        if self.plugin_root is None:
            raise AdapterDiscoveryError(
                "no plugin root is configured; set ACP_ADAPTER_PLUGIN_ROOT",
                code="plugin_root_missing",
            )
        root = self.plugin_root
        if not root.is_dir():
            raise AdapterDiscoveryError(
                f"plugin root does not exist or is not a directory: {root}",
                code="plugin_root_missing",
            )
        discovered: dict[tuple[str, str], DiscoveredAdapter] = {}
        rejected: list[DiscoveryRejection] = []

        builtins = {
            (descriptor.adapter_id, descriptor.version): DiscoveredAdapter(
                descriptor=descriptor,
                descriptor_path="builtin",
                descriptor_digest=descriptor_digest(descriptor),
                entrypoint_path=None,
                entrypoint_digest=None,
                declared_trust_level=descriptor.trust_level,
                effective_trust_level=TrustLevel.TRUSTED_EXPLICIT,
                origin="builtin",
            )
            for descriptor in builtin_adapter_descriptors()
        }
        discovered.update(builtins)

        manifest_paths = sorted(root.glob("*/adapter.json"))
        if (root / "adapter.json").is_file():
            manifest_paths.insert(0, root / "adapter.json")

        for manifest_path in manifest_paths:
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(raw, Mapping):
                    raise AdapterContractError(
                        "invalid_descriptor", "adapter descriptor must be an object"
                    )
                descriptor = AdapterDescriptor.from_dict(raw)
            except (OSError, json.JSONDecodeError, AdapterContractError) as exc:
                rejected.append(
                    DiscoveryRejection(
                        path=str(manifest_path),
                        code=getattr(exc, "code", "descriptor_parse_error"),
                        detail=str(exc),
                    )
                )
                continue
            key = (descriptor.adapter_id, descriptor.version)
            if key in discovered:
                rejected.append(
                    DiscoveryRejection(
                        path=str(manifest_path),
                        code="duplicate_adapter",
                        detail=f"{descriptor.adapter_id}@{descriptor.version} already discovered",
                    )
                )
                continue
            try:
                entrypoint_path, entrypoint_digest = self._resolve_entrypoint(
                    descriptor, manifest_path.parent
                )
            except AdapterContractError as exc:
                rejected.append(
                    DiscoveryRejection(
                        path=str(manifest_path),
                        code=exc.code,
                        detail=exc.detail,
                    )
                )
                continue
            effective_trust = (
                TrustLevel.TRUSTED_EXPLICIT
                if descriptor.adapter_id in self.trusted_adapter_ids
                else TrustLevel.DISCOVER_ONLY
            )
            discovered[key] = DiscoveredAdapter(
                descriptor=descriptor,
                descriptor_path=str(manifest_path),
                descriptor_digest=descriptor_digest(descriptor),
                entrypoint_path=(str(entrypoint_path) if entrypoint_path is not None else None),
                entrypoint_digest=entrypoint_digest,
                declared_trust_level=descriptor.trust_level,
                effective_trust_level=effective_trust,
                origin="external",
            )

        self._discovered = discovered
        report = DiscoveryReport(
            plugin_root=str(root),
            scanned_at=self._now(),
            adapters=tuple(
                sorted(
                    discovered.values(),
                    key=lambda item: (
                        item.descriptor.adapter_id,
                        item.descriptor.version,
                    ),
                )
            ),
            rejected=tuple(rejected),
        )
        self._last_report = report
        return report

    # -- installation / enablement ---------------------------------------

    def install(
        self,
        adapter_id: str,
        version: str,
        *,
        authorization: AdapterAuthorization | None,
        installed_by: str | None = None,
    ) -> InstalledAdapter:
        """Install an exact discovered version after all platform checks."""

        discovered = self._discovered.get((adapter_id, version))
        if discovered is None:
            raise AdapterNotFoundError(
                f"{adapter_id}@{version} was not discovered; run discovery first",
                code="adapter_not_discovered",
            )
        self._require_version_compatible(discovered.descriptor)
        self._require_authorization(
            authorization,
            action="adapter.install",
            adapter_id=adapter_id,
            version=version,
            digest=discovered.descriptor_digest,
        )
        self._require_trust(discovered)
        missing = self._missing_environment(discovered.descriptor)
        if missing:
            raise AdapterEnvironmentMissingError(adapter_id, version, missing)
        if discovered.descriptor.entrypoint is None or discovered.entrypoint_path is None:
            raise AdapterRegistryError(
                f"{adapter_id}@{version} has no entrypoint; external adapters need one",
                code="adapter_entrypoint_missing",
            )
        self._require_no_capability_conflict(discovered)

        existing = self._installed.get((adapter_id, version))
        if existing is not None:
            if (
                existing.descriptor_digest != discovered.descriptor_digest
                or existing.entrypoint_digest != discovered.entrypoint_digest
            ):
                raise AdapterRegistryError(
                    f"{adapter_id}@{version} is already installed with a different digest",
                    code="adapter_digest_conflict",
                )
            return existing

        record = InstalledAdapter(
            descriptor=discovered.descriptor,
            descriptor_digest=discovered.descriptor_digest,
            entrypoint_path=discovered.entrypoint_path,
            entrypoint_digest=discovered.entrypoint_digest,
            installed_at=self._now(),
            installed_by=installed_by or str(authorization.authorized_by),  # type: ignore[union-attr]
            enabled=False,
            effective_trust_level=discovered.effective_trust_level,
        )
        self._installed[(adapter_id, version)] = record
        self._persist_installed(record)
        logger.info(
            "installed infrastructure adapter %s@%s digest=%s",
            adapter_id,
            version,
            discovered.descriptor_digest,
        )
        return record

    def enable(
        self,
        adapter_id: str,
        version: str,
        *,
        authorization: AdapterAuthorization | None,
    ) -> InstalledAdapter:
        """Enable an exact installed version; never substitute another one."""

        record = self.installed(adapter_id, version)
        self._require_authorization(
            authorization,
            action="adapter.enable",
            adapter_id=adapter_id,
            version=version,
            digest=record.descriptor_digest,
        )
        if record.effective_trust_level != TrustLevel.TRUSTED_EXPLICIT:
            raise AdapterUntrustedError(
                f"{adapter_id}@{version} is discover-only and cannot be enabled",
                code="adapter_untrusted",
            )
        missing = self._missing_environment(record.descriptor)
        if missing:
            raise AdapterEnvironmentMissingError(adapter_id, version, missing)
        self._require_no_capability_conflict_for(record)
        for key, installed in list(self._installed.items()):
            if installed.descriptor.adapter_id != adapter_id:
                continue
            if installed.enabled:
                self._installed[key] = _replace_enabled(installed, False)
        enabled_record = _replace_enabled(record, True)
        self._installed[(adapter_id, version)] = enabled_record
        self._active[adapter_id] = version
        self._persist_installed(enabled_record)
        self._persist_active()
        logger.info("enabled infrastructure adapter %s@%s", adapter_id, version)
        return enabled_record

    # -- per-task freeze --------------------------------------------------

    def freeze_for_task(
        self,
        task_id: str,
        attempt: int,
        *,
        adapters: Mapping[str, str] | None = None,
    ) -> TaskAdapterFreeze:
        """Freeze exact adapter versions/digests into one task attempt.

        ``adapters`` maps adapter id -> exact version.  When omitted the active
        version of every installed adapter is used.  Freezing is a read of the
        installation record; it never executes the adapter.
        """

        if not task_id.strip():
            raise AdapterRegistryError("task_id must be non-empty", code="task_freeze_invalid")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise AdapterRegistryError(
                "attempt must be a positive integer", code="task_freeze_invalid"
            )
        if adapters is None:
            selections = {
                adapter_id: version for adapter_id, version in sorted(self._active.items())
            }
        else:
            selections = dict(adapters)
        if not selections:
            raise AdapterRegistryError(
                "no adapters selected for the task freeze", code="task_freeze_invalid"
            )
        frozen: list[FrozenAdapter] = []
        records: dict[str, InstalledAdapter] = {}
        for adapter_id, version in sorted(selections.items()):
            record = self.installed(adapter_id, version)
            records[adapter_id] = record
            frozen.append(
                FrozenAdapter(
                    adapter_id=record.descriptor.adapter_id,
                    version=record.descriptor.version,
                    kind=record.descriptor.kind,
                    descriptor_digest=record.descriptor_digest,
                    entrypoint_digest=record.entrypoint_digest,
                    supported_schema=record.descriptor.supported_schema,
                    environment=record.descriptor.environment,
                    resource_limits=record.descriptor.resource_limits,
                    verification_fixture=record.descriptor.verification_fixture,
                    capabilities=tuple(sorted(record.descriptor.capabilities)),
                    trust_level=record.effective_trust_level,
                    snapshot_dir=(
                        str(self._snapshot_dir(task_id, attempt, adapter_id, version))
                        if self.state_root is not None
                        else None
                    ),
                )
            )
        freeze = TaskAdapterFreeze(
            task_id=task_id,
            attempt=attempt,
            frozen_at=self._now(),
            adapters=tuple(frozen),
        )
        self._task_freezes[(task_id, attempt)] = freeze
        self._persist_task_freeze(freeze)
        self._write_task_snapshot(freeze, records)
        return freeze

    def read_task_freeze(self, task_id: str, attempt: int) -> TaskAdapterFreeze:
        """Read a previously frozen task selection without resolving "latest"."""

        key = (task_id, attempt)
        freeze = self._task_freezes.get(key)
        if freeze is not None:
            return freeze
        if self.state_root is not None:
            path = self._task_freeze_path(task_id, attempt)
            if path.is_file():
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise AdapterRegistryError(
                        f"cannot read task freeze {path}: {exc}",
                        code="task_freeze_unreadable",
                    ) from exc
                freeze = TaskAdapterFreeze.from_dict(document)
                self._task_freezes[key] = freeze
                return freeze
        raise AdapterNotFoundError(
            f"no adapter freeze recorded for task {task_id!r} attempt {attempt}",
            code="task_freeze_not_found",
        )

    def read_frozen_artifact(
        self,
        task_id: str,
        attempt: int,
        adapter_id: str,
    ) -> FrozenArtifact:
        """Return a task's frozen adapter bytes without consulting active state.

        The snapshot was written by :meth:`freeze_for_task`.  If the payload was
        removed or the adapter uninstalled, the snapshot is still served; if no
        snapshot exists the call fails with a named error instead of silently
        resolving to the newest installed version.
        """

        freeze = self.read_task_freeze(task_id, attempt)
        frozen = freeze.adapter(adapter_id)
        cached = self._snapshots.get((task_id, attempt, adapter_id))
        if cached is not None:
            return cached
        if frozen.snapshot_dir is not None:
            artifact = self._read_snapshot(Path(frozen.snapshot_dir), frozen)
            if artifact is not None:
                self._snapshots[(task_id, attempt, adapter_id)] = artifact
                return artifact
        # Backward compatibility for a B21 freeze written before snapshots
        # existed: read the installed record only when it is still present.
        try:
            record = self.installed(adapter_id, frozen.version)
        except AdapterRegistryError as exc:
            raise AdapterRegistryError(
                f"no read-only snapshot for task {task_id!r} attempt {attempt} "
                f"and {adapter_id}@{frozen.version} is not installed",
                code="frozen_artifact_unavailable",
            ) from exc
        if record.descriptor_digest != frozen.descriptor_digest:
            raise AdapterVersionRejectedError(
                f"{adapter_id}@{frozen.version} descriptor digest changed after freeze",
                code="adapter_digest_mismatch",
            )
        entrypoint_bytes: bytes | None = None
        if record.entrypoint_path is not None:
            entrypoint_bytes = Path(record.entrypoint_path).read_bytes()
            actual = hashlib.sha256(entrypoint_bytes).hexdigest()
            if actual != frozen.entrypoint_digest:
                raise AdapterVersionRejectedError(
                    f"{adapter_id}@{frozen.version} entrypoint bytes changed after freeze",
                    code="adapter_entrypoint_digest_mismatch",
                )
        artifact = FrozenArtifact(
            adapter_id=frozen.adapter_id,
            version=frozen.version,
            descriptor_digest=frozen.descriptor_digest,
            entrypoint_digest=frozen.entrypoint_digest,
            descriptor=record.descriptor.to_dict(),
            entrypoint_bytes=entrypoint_bytes,
            source=str(record.entrypoint_path or "installed"),
            snapshot_dir=frozen.snapshot_dir,
        )
        self._snapshots[(task_id, attempt, adapter_id)] = artifact
        return artifact

    def uninstall(
        self,
        adapter_id: str,
        version: str,
        *,
        authorization: AdapterAuthorization | None,
        remove_payload: bool = False,
    ) -> dict[str, Any]:
        """Uninstall an exact installed adapter version.

        Uninstalling removes the installation record and the active pointer,
        never the task freezes/snapshots.  ``remove_payload`` also deletes the
        plugin directory when it lives under the configured plugin root; the
        historical read path still serves the frozen snapshot afterwards.
        """

        record = self.installed(adapter_id, version)
        self._require_authorization(
            authorization,
            action="adapter.uninstall",
            adapter_id=adapter_id,
            version=version,
            digest=record.descriptor_digest,
        )
        payload_removed = False
        payload_path: str | None = None
        if remove_payload and record.entrypoint_path is not None:
            directory = Path(record.entrypoint_path).resolve().parent
            payload_path = str(directory)
            root = self.plugin_root.resolve() if self.plugin_root is not None else None
            if root is not None and directory.is_relative_to(root) and directory != root:
                shutil.rmtree(directory)
                payload_removed = True
            else:
                raise AdapterRegistryError(
                    f"refusing to remove payload outside the plugin root: {directory}",
                    code="adapter_payload_removal_refused",
                )
        self._installed.pop((adapter_id, version), None)
        if self._active.get(adapter_id) == version:
            self._active.pop(adapter_id, None)
        if self.state_root is not None:
            record_path = self._installed_record_path(record)
            if record_path.is_file():
                record_path.unlink()
        self._persist_active()
        logger.info(
            "uninstalled infrastructure adapter %s@%s payload_removed=%s",
            adapter_id,
            version,
            payload_removed,
        )
        return {
            "schema": "acp2.adapter.uninstall.v1",
            "adapter_id": adapter_id,
            "version": version,
            "descriptor_digest": record.descriptor_digest,
            "installed_record_removed": True,
            "payload_removed": payload_removed,
            "payload_path": payload_path,
            "active_after": self._active.get(adapter_id),
        }

    def verify_frozen_adapter(self, frozen: FrozenAdapter) -> InstalledAdapter:
        """Resolve an old task's frozen adapter by exact version and digest."""

        record = self.installed(frozen.adapter_id, frozen.version)
        if record.descriptor_digest != frozen.descriptor_digest:
            raise AdapterVersionRejectedError(
                f"{frozen.adapter_id}@{frozen.version} descriptor digest changed "
                f"({record.descriptor_digest} != {frozen.descriptor_digest})",
                code="adapter_digest_mismatch",
            )
        if record.entrypoint_digest != frozen.entrypoint_digest:
            raise AdapterVersionRejectedError(
                f"{frozen.adapter_id}@{frozen.version} entrypoint digest changed",
                code="adapter_entrypoint_digest_mismatch",
            )
        if record.entrypoint_path:
            actual = _sha256_file(Path(record.entrypoint_path))
            if actual != frozen.entrypoint_digest:
                raise AdapterVersionRejectedError(
                    f"{frozen.adapter_id}@{frozen.version} entrypoint bytes changed "
                    "after the task was frozen",
                    code="adapter_entrypoint_digest_mismatch",
                )
        return record

    # -- explicit execution ----------------------------------------------

    def invoke(
        self,
        adapter_id: str,
        version: str,
        operation: str,
        payload: Mapping[str, Any],
        *,
        allow_execution: bool = False,
    ) -> Any:
        """Execute an enabled adapter operation only after explicit opt-in."""

        if not allow_execution:
            raise AdapterInvocationError(
                "adapter execution requires allow_execution=True; discovery, "
                "install and enable never execute an entrypoint",
                code="execution_requires_explicit_allow",
            )
        record = self.installed(adapter_id, version)
        if self._active.get(adapter_id) != version or not record.enabled:
            raise AdapterVersionRejectedError(
                f"{adapter_id}@{version} is not the enabled version for new work",
                code="adapter_not_enabled",
            )
        return self._execute(record, operation, payload)

    def invoke_frozen(
        self,
        frozen: FrozenAdapter,
        operation: str,
        payload: Mapping[str, Any],
        *,
        allow_execution: bool = False,
    ) -> Any:
        """Execute an old task's frozen adapter (read-back path)."""

        if not allow_execution:
            raise AdapterInvocationError(
                "adapter execution requires allow_execution=True",
                code="execution_requires_explicit_allow",
            )
        record = self.verify_frozen_adapter(frozen)
        return self._execute(record, operation, payload)

    # -- internal ---------------------------------------------------------

    def _register_builtins(self) -> None:
        installed_at = self._now()
        for descriptor in builtin_adapter_descriptors():
            record = InstalledAdapter(
                descriptor=descriptor,
                descriptor_digest=descriptor_digest(descriptor),
                entrypoint_path=None,
                entrypoint_digest=None,
                installed_at=installed_at,
                installed_by="platform",
                enabled=True,
                effective_trust_level=TrustLevel.TRUSTED_EXPLICIT,
            )
            self._installed[record.key] = record
            self._active[descriptor.adapter_id] = descriptor.version

    def _load_state(self) -> None:
        if self.state_root is None or not self.state_root.is_dir():
            return
        installed_root = self.state_root / "installed"
        if installed_root.is_dir():
            for path in sorted(installed_root.glob("*/*.json")):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    record = InstalledAdapter.from_record(document)
                except (OSError, json.JSONDecodeError, KeyError, AdapterContractError):
                    logger.warning("ignoring unreadable installed adapter record %s", path)
                    continue
                if record.descriptor.adapter_id in {
                    descriptor.adapter_id for descriptor in builtin_adapter_descriptors()
                }:
                    continue
                self._installed[record.key] = record
        active_path = self.state_root / "active.json"
        if active_path.is_file():
            try:
                document = json.loads(active_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                document = {}
            raw = document.get("active") if isinstance(document, Mapping) else None
            if isinstance(raw, Mapping):
                for adapter_id, version in raw.items():
                    key = (str(adapter_id), str(version))
                    if key in self._installed:
                        self._active[str(adapter_id)] = str(version)
        tasks_root = self.state_root / "tasks"
        if tasks_root.is_dir():
            for path in tasks_root.glob("*/attempt-*.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    freeze = TaskAdapterFreeze.from_dict(document)
                except (OSError, json.JSONDecodeError, AdapterRegistryError):
                    continue
                self._task_freezes[(freeze.task_id, freeze.attempt)] = freeze

    def _require_version_compatible(self, descriptor: AdapterDescriptor) -> None:
        major = descriptor.version.split(".", 1)[0]
        if major != SUPPORTED_VERSION_MAJOR:
            raise AdapterVersionRejectedError(
                f"{descriptor.adapter_id}@{descriptor.version} has incompatible "
                f"platform major {major}; supported major is {SUPPORTED_VERSION_MAJOR}",
                code="adapter_version_incompatible",
            )

    def _require_authorization(
        self,
        authorization: AdapterAuthorization | None,
        *,
        action: str,
        adapter_id: str,
        version: str,
        digest: str,
    ) -> None:
        if authorization is None:
            raise AdapterAuthorizationError(
                f"{action} for {adapter_id}@{version} needs a server-side authorization fact",
                code="adapter_authorization_required",
            )
        if (
            authorization.action != action
            or authorization.adapter_id != adapter_id
            or authorization.version != version
            or authorization.descriptor_digest != digest
        ):
            raise AdapterAuthorizationError(
                "authorization fact does not match the requested adapter action/identity",
                code="adapter_authorization_invalid",
            )
        if authorization.authorized_by not in self.authorized_principals:
            raise AdapterAuthorizationError(
                f"principal {authorization.authorized_by!r} may not authorize adapters",
                code="adapter_authorization_invalid",
            )
        if not authorization.issued_at:
            raise AdapterAuthorizationError(
                "authorization fact has no issued_at",
                code="adapter_authorization_invalid",
            )
        if authorization.is_expired(self._now()):
            raise AdapterAuthorizationError(
                f"authorization fact expired at {authorization.expires_at}",
                code="adapter_authorization_expired",
            )

    def _require_trust(self, discovered: DiscoveredAdapter) -> None:
        if discovered.effective_trust_level != TrustLevel.TRUSTED_EXPLICIT:
            raise AdapterUntrustedError(
                f"{discovered.descriptor.adapter_id} is discover-only; add it to the "
                "explicit platform trust allow-list to enable it",
                code="adapter_untrusted",
            )

    def _missing_environment(self, descriptor: AdapterDescriptor) -> tuple[str, ...]:
        if not self._enforce_environment:
            return ()
        return tuple(
            requirement
            for requirement in descriptor.environment
            if not self._environment_probe(requirement)
        )

    def _require_no_capability_conflict(self, discovered: DiscoveredAdapter) -> None:
        for capability in sorted(discovered.descriptor.capabilities):
            for record in self._installed.values():
                if (
                    capability in record.descriptor.capabilities
                    and record.descriptor.adapter_id != discovered.descriptor.adapter_id
                ):
                    raise AdapterCapabilityConflictError(
                        capability,
                        f"{record.descriptor.adapter_id}@{record.descriptor.version}",
                        f"{discovered.descriptor.adapter_id}@{discovered.descriptor.version}",
                    )

    def _require_no_capability_conflict_for(self, record: InstalledAdapter) -> None:
        for capability in sorted(record.descriptor.capabilities):
            for other in self._installed.values():
                if (
                    capability in other.descriptor.capabilities
                    and other.descriptor.adapter_id != record.descriptor.adapter_id
                ):
                    raise AdapterCapabilityConflictError(
                        capability,
                        f"{other.descriptor.adapter_id}@{other.descriptor.version}",
                        f"{record.descriptor.adapter_id}@{record.descriptor.version}",
                    )

    def _resolve_entrypoint(
        self,
        descriptor: AdapterDescriptor,
        directory: Path,
    ) -> tuple[Path | None, str | None]:
        entrypoint = descriptor.entrypoint
        if entrypoint is None:
            return None, None
        relative = entrypoint.module
        if not relative.endswith(".py"):
            relative = relative.replace(".", "/") + ".py"
        candidate = (directory / relative).resolve()
        root = directory.resolve()
        if not candidate.is_relative_to(root):
            raise AdapterContractError(
                "invalid_entrypoint",
                f"entrypoint.module escapes its adapter directory: {entrypoint.module!r}",
            )
        if not candidate.is_file():
            raise AdapterContractError(
                "entrypoint_missing",
                f"entrypoint module not found: {candidate}",
            )
        digest = _sha256_file(candidate)
        declared = entrypoint.entrypoint_sha256
        if declared is not None and declared != digest:
            raise AdapterContractError(
                "entrypoint_digest_mismatch",
                f"declared entrypoint sha256 does not match {candidate}",
            )
        return candidate, digest

    def _execute(
        self,
        record: InstalledAdapter,
        operation: str,
        payload: Mapping[str, Any],
    ) -> Any:
        if not isinstance(operation, str) or not operation:
            raise AdapterInvocationError(
                "operation must be a non-empty string", code="invalid_operation"
            )
        if record.effective_trust_level != TrustLevel.TRUSTED_EXPLICIT:
            raise AdapterUntrustedError(
                f"{record.descriptor.adapter_id} is not explicitly trusted",
                code="adapter_untrusted",
            )
        if record.entrypoint_path is None:
            raise AdapterInvocationError(
                f"{record.descriptor.adapter_id} has no executable entrypoint",
                code="adapter_entrypoint_missing",
            )
        path = Path(record.entrypoint_path)
        actual = _sha256_file(path)
        if actual != record.entrypoint_digest:
            raise AdapterInvocationError(
                f"entrypoint bytes changed for {record.descriptor.adapter_id}: "
                f"{actual} != {record.entrypoint_digest}",
                code="adapter_entrypoint_digest_mismatch",
            )
        module_name = "acp_adapter_" + _SAFE_NAME.sub(
            "_", f"{record.descriptor.adapter_id}_{record.descriptor.version}"
        )
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise AdapterInvocationError(
                f"cannot load adapter entrypoint {path}", code="adapter_load_failed"
            )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        attribute = record.descriptor.entrypoint.attribute  # type: ignore[union-attr]
        function = getattr(module, attribute, None)
        if not callable(function):
            raise AdapterInvocationError(
                f"entrypoint attribute {attribute!r} is not callable",
                code="adapter_entrypoint_invalid",
            )
        try:
            result = function(operation, payload)
        except Exception as exc:  # noqa: BLE001 - adapter boundary wraps failures
            raise AdapterInvocationError(
                f"adapter {record.descriptor.adapter_id} operation {operation!r} "
                f"failed: {type(exc).__name__}: {exc}",
                code="adapter_execution_failed",
            ) from exc
        return result

    # -- frozen snapshots -------------------------------------------------

    def _snapshot_dir(
        self,
        task_id: str,
        attempt: int,
        adapter_id: str,
        version: str,
    ) -> Path:
        if self.state_root is None:
            raise AdapterRegistryError(
                "frozen snapshots require a state root", code="state_root_missing"
            )
        return (
            self.state_root
            / "tasks"
            / _SAFE_NAME.sub("_", task_id)
            / f"attempt-{attempt}"
            / "artifacts"
            / _SAFE_NAME.sub("_", adapter_id)
            / _SAFE_NAME.sub("_", version)
        )

    def _write_task_snapshot(
        self,
        freeze: TaskAdapterFreeze,
        records: Mapping[str, InstalledAdapter],
    ) -> None:
        for frozen in freeze.adapters:
            record = records.get(frozen.adapter_id)
            if record is None:
                continue
            descriptor_document = record.descriptor.to_dict()
            entrypoint_bytes: bytes | None = None
            entrypoint_name: str | None = None
            if record.entrypoint_path is not None:
                source = Path(record.entrypoint_path)
                entrypoint_bytes = source.read_bytes()
                entrypoint_name = "entrypoint" + (source.suffix or ".py")
            directory: Path | None = (
                Path(frozen.snapshot_dir) if frozen.snapshot_dir is not None else None
            )
            if directory is not None:
                directory.mkdir(parents=True, exist_ok=True)
                _atomic_write_json(directory / "descriptor.json", descriptor_document)
                if entrypoint_bytes is not None and entrypoint_name is not None:
                    (directory / entrypoint_name).write_bytes(entrypoint_bytes)
                _atomic_write_json(
                    directory / "snapshot.json",
                    {
                        "schema": TASK_ARTIFACT_SCHEMA,
                        "task_id": freeze.task_id,
                        "attempt": freeze.attempt,
                        "adapter_id": frozen.adapter_id,
                        "version": frozen.version,
                        "descriptor_digest": frozen.descriptor_digest,
                        "entrypoint_digest": frozen.entrypoint_digest,
                        "descriptor_file": "descriptor.json",
                        "entrypoint_file": entrypoint_name,
                        "source_descriptor": record.descriptor.to_dict(),
                        "source_entrypoint_path": record.entrypoint_path,
                        "frozen_at": freeze.frozen_at,
                        "read_only": True,
                    },
                )
            self._snapshots[(freeze.task_id, freeze.attempt, frozen.adapter_id)] = FrozenArtifact(
                adapter_id=frozen.adapter_id,
                version=frozen.version,
                descriptor_digest=frozen.descriptor_digest,
                entrypoint_digest=frozen.entrypoint_digest,
                descriptor=descriptor_document,
                entrypoint_bytes=entrypoint_bytes,
                source=str(record.entrypoint_path or "installed"),
                snapshot_dir=frozen.snapshot_dir,
            )

    def _read_snapshot(
        self,
        directory: Path,
        frozen: FrozenAdapter,
    ) -> FrozenArtifact | None:
        descriptor_path = directory / "descriptor.json"
        if not descriptor_path.is_file():
            return None
        try:
            descriptor_document = json.loads(descriptor_path.read_text(encoding="utf-8"))
            metadata = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(descriptor_document, Mapping) or not isinstance(metadata, Mapping):
            return None
        if str(metadata.get("adapter_id")) != frozen.adapter_id:
            raise AdapterRegistryError(
                f"snapshot adapter id mismatch: {metadata.get('adapter_id')!r}",
                code="frozen_artifact_invalid",
            )
        if str(metadata.get("version")) != frozen.version:
            raise AdapterRegistryError(
                f"snapshot version mismatch: {metadata.get('version')!r}",
                code="frozen_artifact_invalid",
            )
        if str(metadata.get("descriptor_digest")) != frozen.descriptor_digest:
            raise AdapterVersionRejectedError(
                f"{frozen.adapter_id}@{frozen.version} snapshot descriptor digest changed",
                code="adapter_digest_mismatch",
            )
        entrypoint_bytes: bytes | None = None
        entrypoint_name = metadata.get("entrypoint_file")
        if isinstance(entrypoint_name, str) and entrypoint_name:
            entrypoint_path = directory / entrypoint_name
            if not entrypoint_path.is_file():
                raise AdapterRegistryError(
                    f"snapshot entrypoint is missing: {entrypoint_path}",
                    code="frozen_artifact_unavailable",
                )
            entrypoint_bytes = entrypoint_path.read_bytes()
            actual = hashlib.sha256(entrypoint_bytes).hexdigest()
            if actual != frozen.entrypoint_digest:
                raise AdapterVersionRejectedError(
                    f"{frozen.adapter_id}@{frozen.version} snapshot entrypoint digest changed",
                    code="adapter_entrypoint_digest_mismatch",
                )
        return FrozenArtifact(
            adapter_id=frozen.adapter_id,
            version=frozen.version,
            descriptor_digest=frozen.descriptor_digest,
            entrypoint_digest=frozen.entrypoint_digest,
            descriptor=descriptor_document,
            entrypoint_bytes=entrypoint_bytes,
            source=str(metadata.get("source_entrypoint_path") or directory),
            snapshot_dir=str(directory),
        )

    def _installed_record_path(self, record: InstalledAdapter) -> Path:
        if self.state_root is None:
            raise AdapterRegistryError(
                "installed records require a state root", code="state_root_missing"
            )
        return (
            self.state_root
            / "installed"
            / _SAFE_NAME.sub("_", record.descriptor.adapter_id)
            / f"{record.descriptor.version}.json"
        )

    # -- persistence ------------------------------------------------------

    def _persist_installed(self, record: InstalledAdapter) -> None:
        if self.state_root is None:
            return
        _atomic_write_json(self._installed_record_path(record), record.to_record())

    def _persist_active(self) -> None:
        if self.state_root is None:
            return
        _atomic_write_json(
            self.state_root / "active.json",
            {"schema": "acp2.adapter.active.v1", "active": dict(sorted(self._active.items()))},
        )

    def _persist_task_freeze(self, freeze: TaskAdapterFreeze) -> None:
        if self.state_root is None:
            return
        _atomic_write_json(
            self._task_freeze_path(freeze.task_id, freeze.attempt),
            freeze.to_dict(),
        )

    def _task_freeze_path(self, task_id: str, attempt: int) -> Path:
        if self.state_root is None:
            raise AdapterRegistryError(
                "task freeze persistence requires a state root",
                code="state_root_missing",
            )
        return self.state_root / "tasks" / _SAFE_NAME.sub("_", task_id) / f"attempt-{attempt}.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _replace_enabled(record: InstalledAdapter, enabled: bool) -> InstalledAdapter:
    return InstalledAdapter(
        descriptor=record.descriptor,
        descriptor_digest=record.descriptor_digest,
        entrypoint_path=record.entrypoint_path,
        entrypoint_digest=record.entrypoint_digest,
        installed_at=record.installed_at,
        installed_by=record.installed_by,
        enabled=enabled,
        effective_trust_level=record.effective_trust_level,
    )


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
