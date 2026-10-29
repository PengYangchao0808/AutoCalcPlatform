"""Third-party science capability admission for the ACP 2.0 platform (B22).

The platform owns *admission*, not science.  This module therefore does four
small things and refuses to do anything else:

1. **Discover** an externally supplied capability descriptor (A's
   ``acp2.science.capability`` v1) and its task binding, reading them as data.
2. **Register/authorize** the exact descriptor digest behind a server-side
   authorization fact and an explicit platform trust allow-list.  Being
   importable never raises trust: an untrusted descriptor is discover-only.
3. **Admit/freeze** one capability registration into a task attempt and write a
   read-only snapshot of the descriptor/binding/view-model bytes, so a later
   unregister or dependency removal cannot make the historical record
   unreadable.
4. **Run** an admitted capability by converting a C form value into a
   ``cccp.calculation.requests.TaskRequest`` through the existing B18 binding
   (:mod:`acp_services.runtime.science_binding`) and dispatching to the
   registered ``cccp.calculation`` task core.  No parser, threshold or result
   semantics live here.

Licences are deliberately separate from trust.  A descriptor with an unknown
licence is still discoverable/readable, but it is never marked approved and
cannot be admitted for execution.  ``approved`` is only ever a server-side
decision with a recorded source.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from acp_services.contracts.adapters import TrustLevel
from acp_services.runtime import science_binding

logger = logging.getLogger(__name__)

__all__ = [
    "CAPABILITY_DISCOVERY_SCHEMA",
    "CAPABILITY_REGISTRATION_SCHEMA",
    "CAPABILITY_FREEZE_SCHEMA",
    "CAPABILITY_ARTIFACT_SCHEMA",
    "APPROVED_LICENSE_STATES",
    "CapabilityError",
    "CapabilityAuthorizationError",
    "CapabilityUntrustedError",
    "CapabilityVersionRejectedError",
    "CapabilityConflictError",
    "CapabilityLicenseError",
    "CapabilityAuthorization",
    "CapabilitySchemaView",
    "ViewModelDiscovery",
    "DiscoveredCapability",
    "CapabilityDiscoveryReport",
    "CapabilityRegistration",
    "CapabilityTaskFreeze",
    "FrozenCapabilityArtifact",
    "CapabilityRegistry",
    "discover_view_model",
    "load_capability_schema",
    "require_capability_authorization",
    "validate_descriptor_against_schema",
    "write_read_only_snapshot",
]


CAPABILITY_DISCOVERY_SCHEMA = "acp2.capability.discovery.v1"
CAPABILITY_REGISTRATION_SCHEMA = "acp2.capability.registration.v1"
CAPABILITY_FREEZE_SCHEMA = "acp2.capability.freeze.v1"
CAPABILITY_ARTIFACT_SCHEMA = "acp2.capability.frozen-artifact.v1"
#: Only an explicit server-side decision may put a row here.
APPROVED_LICENSE_STATES = frozenset({"approved"})
LICENSE_UNKNOWN = "unknown"
LICENSE_NOT_VERIFIED = "NOT_VERIFIED"

_CAPABILITY_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_IMPLEMENTATION_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SCHEMA_VERSION = 1
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CapabilityError(RuntimeError):
    """Base error with a stable machine-readable code."""

    code = "capability_error"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(f"{code or self.code}: {detail}")
        self.code = code or self.code
        self.detail = detail


class CapabilityAuthorizationError(CapabilityError):
    code = "capability_authorization_required"


class CapabilityUntrustedError(CapabilityError):
    code = "capability_untrusted"


class CapabilityVersionRejectedError(CapabilityError):
    code = "capability_version_rejected"


class CapabilityConflictError(CapabilityError):
    code = "capability_conflict"


class CapabilityLicenseError(CapabilityError):
    code = "capability_license_unapproved"


# ---------------------------------------------------------------------------
# Contract views (read A/C schemas as data, never copy their semantics)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilitySchemaView:
    """Structural projection of A's ``capability.schema.json``."""

    schema_id: str
    schema_version: int
    digest: str
    required_fields: tuple[str, ...]
    property_names: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "digest": self.digest,
            "required_fields": list(self.required_fields),
            "property_names": list(self.property_names),
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class ViewModelDiscovery:
    """Structural projection of C's ``experience/view-model.schema.json``."""

    schema_id: str
    schema_version: str
    digest: str
    required_fields: tuple[str, ...]
    ui_dialect_version: str
    supported_keywords: tuple[str, ...]
    source: str
    annotation_only_keywords: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_id": self.schema_id,
            "schema_version": self.schema_version,
            "digest": self.digest,
            "required_fields": list(self.required_fields),
            "ui_dialect_version": self.ui_dialect_version,
            "supported_keywords": list(self.supported_keywords),
            "annotation_only_keywords": list(self.annotation_only_keywords),
            "source": self.source,
        }


def _sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict):
        raise CapabilityError(f"{path} is not a JSON object", code="capability_invalid_json")
    return document, raw


def load_capability_schema(path: str | Path) -> CapabilitySchemaView:
    """Read A's capability schema and expose only its structural vocabulary."""

    source = Path(path)
    document, raw = _read_json(source)
    if document.get("$id") != "https://acp.local/acp2/science/capability.schema.json":
        raise CapabilityVersionRejectedError(
            f"unexpected capability schema $id {document.get('$id')!r}",
            code="capability_schema_id_mismatch",
        )
    required = document.get("required")
    properties = document.get("properties")
    if not isinstance(required, list) or not isinstance(properties, Mapping):
        raise CapabilityError(
            "capability schema has no required/properties block",
            code="capability_schema_invalid",
        )
    return CapabilitySchemaView(
        schema_id="acp2.science.capability.v1",
        schema_version=int(document.get("schema_version", 1)),
        digest=_sha256_bytes(raw),
        required_fields=tuple(str(item) for item in required),
        property_names=tuple(sorted(str(key) for key in properties)),
        source=str(source),
    )


def validate_descriptor_against_schema(
    descriptor: Mapping[str, Any],
    schema: CapabilitySchemaView,
) -> None:
    """Reject structural violations of A's frozen descriptor contract."""

    missing = [name for name in schema.required_fields if name not in descriptor]
    if missing:
        raise CapabilityVersionRejectedError(
            f"capability descriptor is missing required fields: {missing}",
            code="capability_descriptor_incomplete",
        )
    unknown = sorted(set(descriptor) - set(schema.property_names))
    if unknown:
        raise CapabilityVersionRejectedError(
            f"capability descriptor carries unknown top-level fields: {unknown}",
            code="capability_descriptor_unknown_field",
        )
    if descriptor.get("schema") != "acp2.science.capability":
        raise CapabilityVersionRejectedError(
            f"descriptor schema {descriptor.get('schema')!r} is not capability v1",
            code="unsupported_capability",
        )
    if descriptor.get("schema_version") != _SCHEMA_VERSION:
        raise CapabilityVersionRejectedError(
            f"descriptor schema_version {descriptor.get('schema_version')!r} is not executable",
            code="unknown_major_version",
        )
    capability_id = descriptor.get("capability_id")
    if not isinstance(capability_id, str) or not _CAPABILITY_ID.match(capability_id):
        raise CapabilityVersionRejectedError(
            f"invalid capability_id {capability_id!r}", code="capability_id_invalid"
        )
    implementation_id = descriptor.get("implementation_id")
    if not isinstance(implementation_id, str) or not _IMPLEMENTATION_ID.match(implementation_id):
        raise CapabilityVersionRejectedError(
            f"invalid implementation_id {implementation_id!r}",
            code="capability_implementation_invalid",
        )
    compatibility = descriptor.get("compatibility")
    if not isinstance(compatibility, Mapping):
        raise CapabilityVersionRejectedError(
            "descriptor has no compatibility block", code="capability_descriptor_incomplete"
        )
    if compatibility.get("declared_major") != 1:
        raise CapabilityVersionRejectedError(
            f"declared major {compatibility.get('declared_major')!r} is not executable",
            code="unknown_major_version",
        )
    if compatibility.get("unknown_major_version") != "reject_and_never_execute":
        raise CapabilityVersionRejectedError(
            "descriptor does not freeze unknown-major rejection",
            code="capability_compatibility_invalid",
        )
    semantics = descriptor.get("execution_semantics")
    if not isinstance(semantics, Mapping) or semantics.get("kind") not in {
        "compute",
        "pure_analysis",
    }:
        raise CapabilityVersionRejectedError(
            "descriptor has no valid execution_semantics.kind",
            code="capability_execution_semantics_invalid",
        )
    projections = descriptor.get("projections")
    if not isinstance(projections, Mapping):
        raise CapabilityVersionRejectedError(
            "descriptor has no projections block", code="capability_descriptor_incomplete"
        )
    described = projections.get("described")
    if not isinstance(described, Mapping) or described.get("state") != "present":
        raise CapabilityVersionRejectedError(
            "descriptor must project itself as described=present",
            code="capability_projection_invalid",
        )


def discover_view_model(path: str | Path) -> ViewModelDiscovery:
    """Read C's read-only view-model schema and its declared UI dialect."""

    source = Path(path)
    document, raw = _read_json(source)
    if document.get("$id") != "https://acp.local/acp2/experience/view-model.schema.json":
        raise CapabilityVersionRejectedError(
            f"unexpected view-model schema $id {document.get('$id')!r}",
            code="view_model_schema_id_mismatch",
        )
    declared = str(document.get("schema", ""))
    if not declared.startswith("acp2.experience.view-model.v"):
        raise CapabilityVersionRejectedError(
            f"unexpected view-model schema {declared!r}",
            code="view_model_schema_version_mismatch",
        )
    required = document.get("required")
    ui_dialect = document.get("ui_dialect")
    if not isinstance(required, list) or not isinstance(ui_dialect, Mapping):
        raise CapabilityError(
            "view-model schema has no required/ui_dialect block",
            code="view_model_schema_invalid",
        )
    supported = tuple(str(item) for item in ui_dialect.get("supported_keywords", []))
    # ``format`` appears in the delivered schema as an annotation even though
    # C's executable keyword list omits it.  Record that explicitly instead of
    # silently treating it as executable.
    annotation_only: tuple[str, ...] = ()
    raw_text = raw.decode("utf-8")
    if '"format"' in raw_text and "format" not in supported:
        annotation_only = ("format",)
    return ViewModelDiscovery(
        schema_id=declared,
        schema_version=str(document.get("schema_version", "")),
        digest=_sha256_bytes(raw),
        required_fields=tuple(str(item) for item in required),
        ui_dialect_version=str(ui_dialect.get("version", "")),
        supported_keywords=supported,
        annotation_only_keywords=annotation_only,
        source=str(source),
    )


# ---------------------------------------------------------------------------
# Authorization fact and records
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CapabilityAuthorization:
    """Server-side authorization for one capability action."""

    action: str
    capability_id: str
    version: str
    descriptor_digest: str
    authorized_by: str
    issued_at: str
    expires_at: str | None = None

    def is_expired(self, now: str) -> bool:
        return self.expires_at is not None and self.expires_at <= now


@dataclass(frozen=True, slots=True)
class DiscoveredCapability:
    descriptor: Mapping[str, Any]
    task_binding: Mapping[str, Any]
    descriptor_path: str
    binding_path: str
    descriptor_digest: str
    binding_digest: str
    discovery: science_binding.CapabilityDiscovery
    effective_trust_level: str
    license_state: str = LICENSE_UNKNOWN

    @property
    def capability_id(self) -> str:
        return self.discovery.capability_id

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_id, self.version)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "version": self.version,
            "task_kind": self.discovery.task_kind,
            "implementation_id": self.discovery.implementation_id,
            "descriptor_digest": self.descriptor_digest,
            "binding_digest": self.binding_digest,
            "descriptor_path": self.descriptor_path,
            "binding_path": self.binding_path,
            "effective_trust_level": self.effective_trust_level,
            "license_state": self.license_state,
            "executed": False,
        }


@dataclass(frozen=True, slots=True)
class CapabilityDiscoveryReport:
    capability_root: str
    scanned_at: str
    capabilities: tuple[DiscoveredCapability, ...]
    rejected: tuple[dict[str, str], ...] = ()
    view_model: ViewModelDiscovery | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CAPABILITY_DISCOVERY_SCHEMA,
            "capability_root": self.capability_root,
            "scanned_at": self.scanned_at,
            "execution": "none",
            "capabilities": [item.to_metadata() for item in self.capabilities],
            "rejected": list(self.rejected),
            "view_model": self.view_model.to_dict() if self.view_model else None,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRegistration:
    capability_id: str
    version: str
    descriptor_digest: str
    binding_digest: str
    descriptor: Mapping[str, Any]
    task_binding: Mapping[str, Any]
    implementation_id: str
    task_kind: str
    source: str
    license_state: str
    license_source: str | None
    trust_level: str
    registered_at: str
    registered_by: str
    view_model_digest: str | None = None
    view_model_required_fields: tuple[str, ...] = ()
    schema: str = CAPABILITY_REGISTRATION_SCHEMA

    @property
    def key(self) -> tuple[str, str]:
        return (self.capability_id, self.version)

    @property
    def approved(self) -> bool:
        return self.license_state in APPROVED_LICENSE_STATES

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "version": self.version,
            "descriptor_digest": self.descriptor_digest,
            "binding_digest": self.binding_digest,
            "descriptor": dict(self.descriptor),
            "task_binding": dict(self.task_binding),
            "implementation_id": self.implementation_id,
            "task_kind": self.task_kind,
            "source": self.source,
            "license_state": self.license_state,
            "license_source": self.license_source,
            "license_approved": self.approved,
            "trust_level": self.trust_level,
            "registered_at": self.registered_at,
            "registered_by": self.registered_by,
            "view_model_digest": self.view_model_digest,
            "view_model_required_fields": list(self.view_model_required_fields),
        }

    @classmethod
    def from_record(cls, document: Mapping[str, Any]) -> CapabilityRegistration:
        return cls(
            capability_id=str(document["capability_id"]),
            version=str(document["version"]),
            descriptor_digest=str(document["descriptor_digest"]),
            binding_digest=str(document["binding_digest"]),
            descriptor=dict(document["descriptor"]),
            task_binding=dict(document["task_binding"]),
            implementation_id=str(document["implementation_id"]),
            task_kind=str(document["task_kind"]),
            source=str(document["source"]),
            license_state=str(document.get("license_state", LICENSE_UNKNOWN)),
            license_source=(
                str(document["license_source"]) if document.get("license_source") else None
            ),
            trust_level=str(document.get("trust_level", TrustLevel.DISCOVER_ONLY)),
            registered_at=str(document["registered_at"]),
            registered_by=str(document["registered_by"]),
            view_model_digest=(
                str(document["view_model_digest"]) if document.get("view_model_digest") else None
            ),
            view_model_required_fields=tuple(
                str(item) for item in document.get("view_model_required_fields", [])
            ),
        )


@dataclass(frozen=True, slots=True)
class CapabilityTaskFreeze:
    task_id: str
    attempt: int
    frozen_at: str
    registration: CapabilityRegistration
    purpose: str = "execute"
    schema: str = CAPABILITY_FREEZE_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "attempt": self.attempt,
            "frozen_at": self.frozen_at,
            "purpose": self.purpose,
            "registration": self.registration.to_record(),
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> CapabilityTaskFreeze:
        if document.get("schema") != CAPABILITY_FREEZE_SCHEMA:
            raise CapabilityError(
                f"unknown capability freeze schema {document.get('schema')!r}",
                code="capability_freeze_invalid",
            )
        return cls(
            task_id=str(document["task_id"]),
            attempt=int(document["attempt"]),
            frozen_at=str(document.get("frozen_at", "")),
            purpose=str(document.get("purpose", "execute")),
            registration=CapabilityRegistration.from_record(document["registration"]),
        )


@dataclass(frozen=True, slots=True)
class FrozenCapabilityArtifact:
    capability_id: str
    version: str
    descriptor_digest: str
    binding_digest: str
    descriptor: Mapping[str, Any]
    binding: Mapping[str, Any]
    view_model: Mapping[str, Any] | None
    source: str
    snapshot_dir: str | None
    read_only: bool = True
    schema: str = CAPABILITY_ARTIFACT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "capability_id": self.capability_id,
            "version": self.version,
            "descriptor_digest": self.descriptor_digest,
            "binding_digest": self.binding_digest,
            "descriptor": dict(self.descriptor),
            "binding": dict(self.binding),
            "view_model": dict(self.view_model) if self.view_model else None,
            "source": self.source,
            "snapshot_dir": self.snapshot_dir,
            "read_only": self.read_only,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """Admission-only registry for externally supplied science capabilities."""

    def __init__(
        self,
        *,
        capability_root: str | Path | None = None,
        state_root: str | Path | None = None,
        schema_path: str | Path | None = None,
        view_model_path: str | Path | None = None,
        trusted_capabilities: Sequence[str] = (),
        authorized_principals: Sequence[str] = ("controller",),
        now: Any | None = None,
    ) -> None:
        self.capability_root = Path(capability_root) if capability_root is not None else None
        self.state_root = Path(state_root) if state_root is not None else None
        self.schema_path = Path(schema_path) if schema_path is not None else None
        self.view_model_path = Path(view_model_path) if view_model_path is not None else None
        self.trusted_capabilities = frozenset(trusted_capabilities)
        self.authorized_principals = frozenset(authorized_principals)
        self._now = now or _utcnow
        self._schema: CapabilitySchemaView | None = None
        self._view_model: ViewModelDiscovery | None = None
        self._discovered: dict[tuple[str, str], DiscoveredCapability] = {}
        self._registered: dict[tuple[str, str], CapabilityRegistration] = {}
        self._freezes: dict[tuple[str, int], CapabilityTaskFreeze] = {}
        self._last_report: CapabilityDiscoveryReport | None = None
        if self.schema_path is not None:
            self._schema = load_capability_schema(self.schema_path)
        if self.view_model_path is not None:
            self._view_model = discover_view_model(self.view_model_path)
        self._load_state()

    # -- introspection ---------------------------------------------------

    @property
    def last_discovery(self) -> CapabilityDiscoveryReport | None:
        return self._last_report

    @property
    def view_model(self) -> ViewModelDiscovery | None:
        return self._view_model

    @property
    def schema(self) -> CapabilitySchemaView | None:
        return self._schema

    def registered(self, capability_id: str, version: str = "1.0.0") -> CapabilityRegistration:
        record = self._registered.get((capability_id, version))
        if record is None:
            raise CapabilityVersionRejectedError(
                f"{capability_id}@{version} is not registered",
                code="capability_not_registered",
            )
        return record

    def registrations(self) -> tuple[CapabilityRegistration, ...]:
        return tuple(sorted(self._registered.values(), key=lambda item: item.key))

    # -- discovery -------------------------------------------------------

    def discover(self) -> CapabilityDiscoveryReport:
        if self.capability_root is None:
            raise CapabilityError(
                "no capability root is configured", code="capability_root_missing"
            )
        root = self.capability_root
        if not root.is_dir():
            raise CapabilityError(
                f"capability root is not a directory: {root}", code="capability_root_missing"
            )
        if self._schema is None:
            raise CapabilityError(
                "capability schema is required for discovery",
                code="capability_schema_missing",
            )
        discovered: dict[tuple[str, str], DiscoveredCapability] = {}
        rejected: list[dict[str, str]] = []
        manifest_paths = sorted(root.glob("*/capability.json"))
        if (root / "capability.json").is_file():
            manifest_paths.insert(0, root / "capability.json")
        for manifest_path in manifest_paths:
            try:
                descriptor, descriptor_raw = _read_json(manifest_path)
                validate_descriptor_against_schema(descriptor, self._schema)
                binding_path = manifest_path.with_name("task-binding.json")
                if not binding_path.is_file():
                    raise CapabilityError(
                        f"task binding missing next to {manifest_path}",
                        code="capability_binding_missing",
                    )
                binding, binding_raw = _read_json(binding_path)
                projection = science_binding.discover_capability(descriptor, binding)
            except (OSError, json.JSONDecodeError, ValueError, CapabilityError) as exc:
                rejected.append(
                    {
                        "path": str(manifest_path),
                        "code": getattr(exc, "code", "capability_discovery_error"),
                        "detail": str(exc),
                    }
                )
                continue
            item = DiscoveredCapability(
                descriptor=descriptor,
                task_binding=binding,
                descriptor_path=str(manifest_path),
                binding_path=str(binding_path),
                descriptor_digest=_sha256_bytes(descriptor_raw),
                binding_digest=_sha256_bytes(binding_raw),
                discovery=projection,
                effective_trust_level=(
                    TrustLevel.TRUSTED_EXPLICIT
                    if projection.capability_id in self.trusted_capabilities
                    else TrustLevel.DISCOVER_ONLY
                ),
            )
            discovered[item.key] = item
        self._discovered = discovered
        report = CapabilityDiscoveryReport(
            capability_root=str(root),
            scanned_at=self._now(),
            capabilities=tuple(sorted(discovered.values(), key=lambda item: item.key)),
            rejected=tuple(rejected),
            view_model=self._view_model,
        )
        self._last_report = report
        return report

    # -- registration / authorization -----------------------------------

    def register(
        self,
        capability_id: str,
        *,
        authorization: CapabilityAuthorization | None,
        version: str = "1.0.0",
        source: str | None = None,
        license_state: str = LICENSE_UNKNOWN,
        license_source: str | None = None,
    ) -> CapabilityRegistration:
        item = self._discovered.get((capability_id, version))
        if item is None:
            raise CapabilityVersionRejectedError(
                f"{capability_id}@{version} was not discovered; run discovery first",
                code="capability_not_discovered",
            )
        if item.effective_trust_level != TrustLevel.TRUSTED_EXPLICIT:
            raise CapabilityUntrustedError(
                f"{capability_id} is discover-only; an explicit trust allow-list entry is required",
                code="capability_untrusted",
            )
        self._require_authorization(
            authorization,
            action="capability.register",
            capability_id=capability_id,
            version=version,
            digest=item.descriptor_digest,
        )
        existing = self._registered.get((capability_id, version))
        if existing is not None:
            if (
                existing.descriptor_digest != item.descriptor_digest
                or existing.binding_digest != item.binding_digest
            ):
                raise CapabilityConflictError(
                    f"{capability_id}@{version} is already registered with a different digest",
                    code="capability_digest_conflict",
                )
            return existing
        record = CapabilityRegistration(
            capability_id=item.capability_id,
            version=version,
            descriptor_digest=item.descriptor_digest,
            binding_digest=item.binding_digest,
            descriptor=dict(item.descriptor),
            task_binding=dict(item.task_binding),
            implementation_id=item.discovery.implementation_id,
            task_kind=item.discovery.task_kind,
            source=source or item.descriptor_path,
            license_state=license_state,
            license_source=license_source,
            trust_level=item.effective_trust_level,
            registered_at=self._now(),
            registered_by=str(authorization.authorized_by),  # type: ignore[union-attr]
            view_model_digest=(self._view_model.digest if self._view_model else None),
            view_model_required_fields=(
                self._view_model.required_fields if self._view_model else ()
            ),
        )
        self._registered[(capability_id, version)] = record
        self._persist_registration(record)
        logger.info(
            "registered third-party capability %s@%s digest=%s license=%s approved=%s",
            capability_id,
            version,
            item.descriptor_digest,
            license_state,
            record.approved,
        )
        return record

    def admit_for_task(
        self,
        task_id: str,
        attempt: int,
        capability_id: str,
        *,
        authorization: CapabilityAuthorization | None,
        version: str = "1.0.0",
        purpose: str = "execute",
    ) -> CapabilityTaskFreeze:
        """Freeze one registration into a task attempt (and snapshot it)."""

        record = self.registered(capability_id, version)
        self._require_authorization(
            authorization,
            action="capability.admit",
            capability_id=capability_id,
            version=version,
            digest=record.descriptor_digest,
        )
        if not task_id.strip():
            raise CapabilityError("task_id must be non-empty", code="capability_freeze_invalid")
        if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
            raise CapabilityError(
                "attempt must be a positive integer", code="capability_freeze_invalid"
            )
        if purpose not in {"execute", "read_only"}:
            raise CapabilityError(
                f"unknown admission purpose {purpose!r}", code="capability_freeze_invalid"
            )
        if purpose == "execute" and not record.approved:
            raise CapabilityLicenseError(
                f"{capability_id}@{version} licence state {record.license_state!r} is not "
                "approved; read-only admission only",
                code="capability_license_unapproved",
            )
        freeze = CapabilityTaskFreeze(
            task_id=task_id,
            attempt=attempt,
            frozen_at=self._now(),
            registration=record,
            purpose=purpose,
        )
        self._freezes[(task_id, attempt)] = freeze
        self._persist_freeze(freeze)
        self._snapshot_freeze(freeze)
        return freeze

    def read_task_freeze(self, task_id: str, attempt: int) -> CapabilityTaskFreeze:
        key = (task_id, attempt)
        freeze = self._freezes.get(key)
        if freeze is not None:
            return freeze
        if self.state_root is not None:
            path = self._freeze_path(task_id, attempt)
            if path.is_file():
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise CapabilityError(
                        f"cannot read capability freeze {path}: {exc}",
                        code="capability_freeze_unreadable",
                    ) from exc
                freeze = CapabilityTaskFreeze.from_dict(document)
                self._freezes[key] = freeze
                return freeze
        raise CapabilityVersionRejectedError(
            f"no capability freeze for task {task_id!r} attempt {attempt}",
            code="capability_freeze_not_found",
        )

    def read_frozen_artifact(
        self,
        task_id: str,
        attempt: int,
    ) -> FrozenCapabilityArtifact:
        """Serve the task's read-only snapshot; never resolves the active version."""

        freeze = self.read_task_freeze(task_id, attempt)
        record = freeze.registration
        snapshot_dir = self._artifact_dir(freeze)
        if snapshot_dir is not None and snapshot_dir.is_dir():
            descriptor_path = snapshot_dir / "descriptor.json"
            binding_path = snapshot_dir / "task-binding.json"
            try:
                descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
                binding = json.loads(binding_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise CapabilityError(
                    f"frozen capability snapshot is unreadable: {exc}",
                    code="capability_artifact_unavailable",
                ) from exc
            # The snapshot's canonical digest is not asserted against the
            # source-byte digest: JSON re-serialisation is not byte identity.
            if descriptor.get("capability_id") != record.capability_id:
                raise CapabilityVersionRejectedError(
                    "frozen snapshot capability id does not match the task freeze",
                    code="capability_artifact_invalid",
                )
            view_model: Mapping[str, Any] | None = None
            view_path = snapshot_dir / "view-model.json"
            if view_path.is_file():
                try:
                    view_model = json.loads(view_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    view_model = None
            return FrozenCapabilityArtifact(
                capability_id=record.capability_id,
                version=record.version,
                descriptor_digest=record.descriptor_digest,
                binding_digest=record.binding_digest,
                descriptor=descriptor,
                binding=binding,
                view_model=view_model,
                source=record.source,
                snapshot_dir=str(snapshot_dir),
                read_only=True,
            )
        # Fallback only for a pre-snapshot record: serve the in-memory copy.
        if record.descriptor_digest:
            return FrozenCapabilityArtifact(
                capability_id=record.capability_id,
                version=record.version,
                descriptor_digest=record.descriptor_digest,
                binding_digest=record.binding_digest,
                descriptor=dict(record.descriptor),
                binding=dict(record.task_binding),
                view_model=None,
                source=record.source,
                snapshot_dir=None,
                read_only=True,
            )
        raise CapabilityError(
            "no frozen capability artifact is available",
            code="capability_artifact_unavailable",
        )

    def unregister(
        self,
        capability_id: str,
        *,
        authorization: CapabilityAuthorization | None,
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        record = self.registered(capability_id, version)
        self._require_authorization(
            authorization,
            action="capability.unregister",
            capability_id=capability_id,
            version=version,
            digest=record.descriptor_digest,
        )
        self._registered.pop((capability_id, version), None)
        if self.state_root is not None:
            path = self._registration_path(record)
            if path.is_file():
                path.unlink()
        logger.info("unregistered third-party capability %s@%s", capability_id, version)
        return {
            "schema": "acp2.capability.unregister.v1",
            "capability_id": capability_id,
            "version": version,
            "descriptor_digest": record.descriptor_digest,
            "registration_removed": True,
            "license_state": record.license_state,
            "license_approved": record.approved,
        }

    # -- execution through the existing B18/CCCP path --------------------

    def run_frozen(
        self,
        task_id: str,
        attempt: int,
        form: Mapping[str, Any],
        *,
        allow_execution: bool = False,
        context: Any | None = None,
    ) -> Any:
        """Execute a frozen capability through ``cccp.calculation``.

        This is the only execution seam.  The form is mapped by the existing
        :func:`science_binding.build_task_request`; dispatch selects the
        registered ``run_<task_kind>`` core from ``cccp.calculation.tasks``.
        """

        if not allow_execution:
            raise CapabilityError(
                "capability execution requires allow_execution=True",
                code="execution_requires_explicit_allow",
            )
        freeze = self.read_task_freeze(task_id, attempt)
        record = freeze.registration
        if freeze.purpose != "execute":
            raise CapabilityError(
                "this task froze the capability for read-only admission",
                code="capability_not_admitted_for_execution",
            )
        if record.trust_level != TrustLevel.TRUSTED_EXPLICIT:
            raise CapabilityUntrustedError(
                f"{record.capability_id} is not explicitly trusted",
                code="capability_untrusted",
            )
        if not record.approved:
            raise CapabilityLicenseError(
                f"{record.capability_id}@{record.version} licence state "
                f"{record.license_state!r} is not approved",
                code="capability_license_unapproved",
            )
        # Rebuild the discovery projection from the frozen snapshot so a later
        # descriptor edit cannot change what this task executes.
        artifact = self.read_frozen_artifact(task_id, attempt)
        projection = science_binding.discover_capability(artifact.descriptor, artifact.binding)
        request = science_binding.build_task_request(form, projection)
        function = _resolve_run_function(record.implementation_id, record.task_kind)
        return function(request, context=context)

    # -- internal --------------------------------------------------------

    def _require_authorization(
        self,
        authorization: CapabilityAuthorization | None,
        *,
        action: str,
        capability_id: str,
        version: str,
        digest: str,
    ) -> None:
        require_capability_authorization(
            authorization,
            action=action,
            capability_id=capability_id,
            version=version,
            digest=digest,
            authorized_principals=self.authorized_principals,
            now=self._now(),
        )

    def _load_state(self) -> None:
        if self.state_root is None or not self.state_root.is_dir():
            return
        root = self.state_root / "capabilities"
        if root.is_dir():
            for path in sorted(root.glob("*/*.json")):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    record = CapabilityRegistration.from_record(document)
                except (OSError, json.JSONDecodeError, KeyError, TypeError):
                    logger.warning("ignoring unreadable capability registration %s", path)
                    continue
                self._registered[record.key] = record
        tasks = self.state_root / "tasks"
        if tasks.is_dir():
            for path in tasks.glob("*/attempt-*.json"):
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                    freeze = CapabilityTaskFreeze.from_dict(document)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, CapabilityError):
                    continue
                self._freezes[(freeze.task_id, freeze.attempt)] = freeze

    def _registration_path(self, record: CapabilityRegistration) -> Path:
        if self.state_root is None:
            raise CapabilityError(
                "capability registration persistence needs a state root",
                code="state_root_missing",
            )
        return (
            self.state_root
            / "capabilities"
            / _SAFE_NAME.sub("_", record.capability_id)
            / f"{_SAFE_NAME.sub('_', record.version)}.json"
        )

    def _persist_registration(self, record: CapabilityRegistration) -> None:
        if self.state_root is None:
            return
        _atomic_write_json(self._registration_path(record), record.to_record())

    def _freeze_path(self, task_id: str, attempt: int) -> Path:
        if self.state_root is None:
            raise CapabilityError(
                "capability freeze persistence needs a state root",
                code="state_root_missing",
            )
        return self.state_root / "tasks" / _SAFE_NAME.sub("_", task_id) / f"attempt-{attempt}.json"

    def _persist_freeze(self, freeze: CapabilityTaskFreeze) -> None:
        if self.state_root is None:
            return
        _atomic_write_json(self._freeze_path(freeze.task_id, freeze.attempt), freeze.to_dict())

    def _artifact_dir(self, freeze: CapabilityTaskFreeze) -> Path | None:
        if self.state_root is None:
            return None
        record = freeze.registration
        return (
            self.state_root
            / "tasks"
            / _SAFE_NAME.sub("_", freeze.task_id)
            / f"attempt-{freeze.attempt}"
            / "capability"
            / _SAFE_NAME.sub("_", record.capability_id)
        )

    def _snapshot_freeze(self, freeze: CapabilityTaskFreeze) -> None:
        directory = self._artifact_dir(freeze)
        if directory is None:
            return
        record = freeze.registration
        documents: dict[str, Mapping[str, Any]] = {
            "descriptor": dict(record.descriptor),
            "task-binding": dict(record.task_binding),
            "snapshot": {
                "schema": CAPABILITY_ARTIFACT_SCHEMA,
                "task_id": freeze.task_id,
                "attempt": freeze.attempt,
                "capability_id": record.capability_id,
                "version": record.version,
                "descriptor_digest": record.descriptor_digest,
                "binding_digest": record.binding_digest,
                "view_model_digest": record.view_model_digest,
                "license_state": record.license_state,
                "license_approved": record.approved,
                "trust_level": record.trust_level,
                "frozen_at": freeze.frozen_at,
                "purpose": freeze.purpose,
                "read_only": True,
            },
        }
        if self.view_model_path is not None and self.view_model_path.is_file():
            view_document, _ = _read_json(self.view_model_path)
            documents["view-model"] = view_document
        write_read_only_snapshot(directory, documents)


def require_capability_authorization(
    authorization: CapabilityAuthorization | None,
    *,
    action: str,
    capability_id: str,
    version: str,
    digest: str,
    authorized_principals: Iterable[str],
    now: str,
) -> None:
    """Verify one server-side authorization fact against the requested action.

    This is the single authorization predicate of the platform capability
    authority: :class:`CapabilityRegistry` and the installed-plugin enablement
    ledger both call it, so "who may authorize what, bound to which digest" has
    exactly one implementation.

    Raises:
        CapabilityAuthorizationError: absent, mismatched, issued by an
            unauthorized principal, undated or expired.
    """
    principals = frozenset(authorized_principals)
    if authorization is None:
        raise CapabilityAuthorizationError(
            f"{action} for {capability_id}@{version} needs a server-side authorization fact",
            code="capability_authorization_required",
        )
    if (
        authorization.action != action
        or authorization.capability_id != capability_id
        or authorization.version != version
        or authorization.descriptor_digest != digest
    ):
        raise CapabilityAuthorizationError(
            "authorization fact does not match the requested capability action/identity",
            code="capability_authorization_invalid",
        )
    if authorization.authorized_by not in principals:
        raise CapabilityAuthorizationError(
            f"principal {authorization.authorized_by!r} may not authorize capabilities",
            code="capability_authorization_invalid",
        )
    if not authorization.issued_at:
        raise CapabilityAuthorizationError(
            "authorization fact has no issued_at",
            code="capability_authorization_invalid",
        )
    if authorization.is_expired(now):
        raise CapabilityAuthorizationError(
            f"authorization fact expired at {authorization.expires_at}",
            code="capability_authorization_expired",
        )


def write_read_only_snapshot(
    directory: str | Path,
    documents: Mapping[str, Mapping[str, Any]],
) -> Path:
    """Write one read-only capability snapshot directory atomically.

    ``documents`` maps a snapshot document name to its JSON document; each name
    becomes ``<name>.json`` in *directory*.  This is the one snapshot writer
    shared by the descriptor-based capability freeze and the installed-plugin
    enablement ledger, so both historical records have the same layout rules
    (atomic ``os.replace``, sorted keys, ``read_only``).

    Returns:
        The snapshot directory.
    """
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    for name, document in documents.items():
        _atomic_write_json(target / f"{name}.json", dict(document))
    return target


def _resolve_run_function(implementation_id: str, task_kind: str) -> Any:
    """Import the registered task core lazily; no science logic here."""

    module_name, _, class_name = implementation_id.rpartition(".")
    if not module_name or not class_name:
        raise CapabilityError(
            f"implementation id {implementation_id!r} is not importable",
            code="capability_implementation_invalid",
        )
    function_name = "run_" + task_kind
    import importlib

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise CapabilityError(
            f"implementation module {module_name!r} is unavailable: {exc}",
            code="capability_implementation_missing",
        ) from exc
    function = getattr(module, function_name, None)
    if not callable(function):
        raise CapabilityError(
            f"implementation {module_name}.{function_name} is not callable",
            code="capability_implementation_missing",
        )
    return function


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)
