"""Platform infrastructure-adapter contract (B21).

This module describes **infrastructure** capabilities only — storage, transfer,
identity and the existing Local/LSF scheduler adapters.  A platform adapter is
metadata plus an optional trusted entrypoint; it is deliberately **not** a
science plugin.  Scientific parsers, thresholds and result semantics stay in
their owning scientific packages (A's recipe/plugin surface), never here.

Hard properties encoded below
-----------------------------
* **Metadata is inert.**  An :class:`AdapterDescriptor` is a frozen value
  object.  Parsing/validating it never imports or executes the declared
  entrypoint; that is the registry's explicit, separately-authorized job.
* **Discovery is not trust.**  The default trust level is
  :attr:`TrustLevel.DISCOVER_ONLY`.  Only an explicit platform trust decision
  (owned by the registry/composition root) may enable an adapter.
* **No isolation claim from metadata.**  An in-process adapter is not a
  sandbox; a descriptor that claims process/OS isolation is rejected.  Process
  isolation is a separate capability and is reported ``NOT_VERIFIED`` until it
  is actually provided by the execution environment.
* **Science is rejected at the boundary.**  Capability names are namespaced by
  infrastructure kind, and a small deny-list rejects obviously scientific
  capabilities/identifiers before an adapter can be installed.

The frozen vocabulary is intentionally small so B22 (upgrade/rollback) can add
versions and B23 (multi-project) can consume the frozen facts without a second
registry authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ADAPTER_SCHEMA",
    "SUPPORTED_ADAPTER_SCHEMAS",
    "FORBIDDEN_SCIENCE_TOKENS",
    "AdapterKind",
    "TrustLevel",
    "AdapterContractError",
    "AdapterResourceLimits",
    "AdapterEntrypoint",
    "AdapterDescriptor",
    "canonical_descriptor_payload",
    "descriptor_digest",
    "builtin_adapter_descriptors",
]


ADAPTER_SCHEMA = "acp2.adapter.v1"
SUPPORTED_ADAPTER_SCHEMAS = frozenset({ADAPTER_SCHEMA})

_ADAPTER_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+$")
_SEMVER = re.compile(r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$")
_CAPABILITY = re.compile(r"^(storage|transfer|identity|scheduler)\.[a-z0-9][a-z0-9._-]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ENVIRONMENT = re.compile(r"^(env|path|command|platform|cluster):[A-Za-z0-9_./:+@-]+$")

#: Scientific vocabulary that must never appear on the *platform* adapter
#: boundary.  Matching is case-insensitive and token-boundary based.
FORBIDDEN_SCIENCE_TOKENS = frozenset(
    {
        "casscf",
        "chemistry",
        "dft",
        "energy",
        "frequency",
        "geometry",
        "molecule",
        "nmr",
        "orbital",
        "quantum",
        "science",
        "scientific",
        "spectrum",
        "thermo",
    }
)


class AdapterKind(str):
    """Infrastructure capability families a platform adapter may expose."""

    STORAGE = "storage"
    TRANSFER = "transfer"
    IDENTITY = "identity"
    SCHEDULER = "scheduler"

    ALL = frozenset({STORAGE, TRANSFER, IDENTITY, SCHEDULER})


class TrustLevel(str):
    """Adapter trust vocabulary.

    ``DISCOVER_ONLY`` is the only safe default: metadata may be read, but the
    adapter can never be installed/enabled without a separate platform trust
    decision.  ``TRUSTED_EXPLICIT`` is granted by the composition root, never
    self-asserted by a plugin descriptor alone.
    """

    DISCOVER_ONLY = "discover-only"
    TRUSTED_EXPLICIT = "trusted-explicit"

    ALL = frozenset({DISCOVER_ONLY, TRUSTED_EXPLICIT})


class AdapterContractError(ValueError):
    """A descriptor violates the platform adapter contract."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class AdapterResourceLimits:
    """Declared ceilings for one adapter operation.

    These are *metadata*: the registry records and validates them, while an
    execution environment that actually enforces them must report its own
    isolation status.  A resource ceiling is not an isolation boundary.
    """

    max_cpu_seconds: int = 60
    max_memory_bytes: int = 64 * 1024 * 1024
    max_concurrency: int = 1
    max_io_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_cpu_seconds",
            "max_memory_bytes",
            "max_concurrency",
            "max_io_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise AdapterContractError(
                    "invalid_resource_limit",
                    f"resource_limits.{name} must be a positive integer",
                )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_cpu_seconds": self.max_cpu_seconds,
            "max_memory_bytes": self.max_memory_bytes,
            "max_concurrency": self.max_concurrency,
            "max_io_bytes": self.max_io_bytes,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AdapterResourceLimits:
        _reject_unknown(
            document,
            {
                "max_cpu_seconds",
                "max_memory_bytes",
                "max_concurrency",
                "max_io_bytes",
            },
            "resource_limits",
        )
        return cls(
            max_cpu_seconds=document.get("max_cpu_seconds", 60),
            max_memory_bytes=document.get("max_memory_bytes", 64 * 1024 * 1024),
            max_concurrency=document.get("max_concurrency", 1),
            max_io_bytes=document.get("max_io_bytes", 8 * 1024 * 1024),
        )


@dataclass(frozen=True)
class AdapterEntrypoint:
    """Explicit entrypoint metadata for an externally installed adapter.

    Nothing in this module (or in discovery) imports the module.  The registry
    only resolves and executes it after install/enable plus an explicit
    ``allow_execution`` flag.
    """

    module: str
    attribute: str
    entrypoint_sha256: str | None = None

    def __post_init__(self) -> None:
        module = self.module.strip()
        if not module or module.startswith(("/", "\\")) or ".." in module.split("/"):
            raise AdapterContractError(
                "invalid_entrypoint",
                "entrypoint.module must be a relative module path",
            )
        if not _IDENTIFIER.match(self.attribute):
            raise AdapterContractError(
                "invalid_entrypoint",
                f"entrypoint.attribute is not an identifier: {self.attribute!r}",
            )
        digest = self.entrypoint_sha256
        if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AdapterContractError(
                "invalid_entrypoint",
                "entrypoint.entrypoint_sha256 must be 64 lowercase hex characters",
            )

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "module": self.module,
            "attribute": self.attribute,
        }
        if self.entrypoint_sha256 is not None:
            document["entrypoint_sha256"] = self.entrypoint_sha256
        return document

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AdapterEntrypoint:
        _reject_unknown(
            document,
            {"module", "attribute", "entrypoint_sha256"},
            "entrypoint",
        )
        module = document.get("module")
        attribute = document.get("attribute")
        digest = document.get("entrypoint_sha256")
        if not isinstance(module, str) or not isinstance(attribute, str):
            raise AdapterContractError(
                "invalid_entrypoint",
                "entrypoint.module and entrypoint.attribute must be strings",
            )
        if digest is not None and not isinstance(digest, str):
            raise AdapterContractError(
                "invalid_entrypoint",
                "entrypoint.entrypoint_sha256 must be a string when present",
            )
        return cls(module=module, attribute=attribute, entrypoint_sha256=digest)


_DESCRIPTOR_FIELDS = frozenset(
    {
        "descriptor_schema",
        "adapter_id",
        "version",
        "kind",
        "supported_schema",
        "environment",
        "resource_limits",
        "verification_fixture",
        "capabilities",
        "trust_level",
        "entrypoint",
        "description",
        "claims_process_isolation",
    }
)


@dataclass(frozen=True)
class AdapterDescriptor:
    """Frozen infrastructure-adapter metadata.

    No field is executable.  ``entrypoint`` is inert metadata and is never
    imported by descriptor validation or discovery.
    """

    adapter_id: str
    version: str
    kind: str
    supported_schema: tuple[str, ...]
    environment: tuple[str, ...] = ()
    resource_limits: AdapterResourceLimits = AdapterResourceLimits()
    verification_fixture: str = ""
    capabilities: frozenset[str] = frozenset()
    trust_level: str = TrustLevel.DISCOVER_ONLY
    entrypoint: AdapterEntrypoint | None = None
    description: str = ""
    claims_process_isolation: bool = False
    descriptor_schema: str = ADAPTER_SCHEMA

    def __post_init__(self) -> None:
        if self.descriptor_schema not in SUPPORTED_ADAPTER_SCHEMAS:
            raise AdapterContractError(
                "unsupported_descriptor_schema",
                f"descriptor_schema={self.descriptor_schema!r} is not supported",
            )
        if not _ADAPTER_ID.match(self.adapter_id):
            raise AdapterContractError(
                "invalid_adapter_id",
                f"adapter_id={self.adapter_id!r} is not a namespaced lowercase id",
            )
        if not _SEMVER.match(self.version):
            raise AdapterContractError(
                "invalid_adapter_version",
                f"version={self.version!r} is not strict semver MAJOR.MINOR.PATCH",
            )
        if self.kind not in AdapterKind.ALL:
            raise AdapterContractError(
                "invalid_adapter_kind",
                f"kind={self.kind!r} is not an infrastructure kind",
            )
        if not self.supported_schema:
            raise AdapterContractError(
                "missing_supported_schema",
                "supported_schema must list at least one schema id",
            )
        for schema in self.supported_schema:
            if not isinstance(schema, str) or not schema:
                raise AdapterContractError(
                    "invalid_supported_schema",
                    "supported_schema entries must be non-empty strings",
                )
            if schema not in SUPPORTED_ADAPTER_SCHEMAS:
                raise AdapterContractError(
                    "unsupported_adapter_schema",
                    f"supported_schema={schema!r} is not supported by this platform",
                )
        for requirement in self.environment:
            if not isinstance(requirement, str) or not _ENVIRONMENT.match(requirement):
                raise AdapterContractError(
                    "invalid_environment_requirement",
                    f"environment requirement {requirement!r} must be "
                    "env:/path:/command:/platform:/cluster:<name>",
                )
        if not self.verification_fixture.strip():
            raise AdapterContractError(
                "missing_verification_fixture",
                "verification_fixture is required",
            )
        if ".." in self.verification_fixture.split("/"):
            raise AdapterContractError(
                "invalid_verification_fixture",
                "verification_fixture must not traverse parent directories",
            )
        if not self.capabilities:
            raise AdapterContractError(
                "missing_capabilities",
                "capabilities must contain at least one infrastructure capability",
            )
        for capability in self.capabilities:
            if not isinstance(capability, str) or not _CAPABILITY.match(capability):
                raise AdapterContractError(
                    "invalid_capability",
                    f"capability={capability!r} is not an infrastructure capability",
                )
        if self.trust_level not in TrustLevel.ALL:
            raise AdapterContractError(
                "invalid_trust_level",
                f"trust_level={self.trust_level!r} is unknown",
            )
        if self.claims_process_isolation:
            raise AdapterContractError(
                "adapter_isolation_claim_rejected",
                "an in-process adapter is not a sandbox; process/OS isolation is a "
                "separate capability and may not be claimed in metadata",
            )
        science_hits = sorted(
            token for token in _SCIENCE_HAYSTACK(self) if token in FORBIDDEN_SCIENCE_TOKENS
        )
        if science_hits:
            raise AdapterContractError(
                "science_capability_forbidden",
                "platform adapters must not carry scientific logic; "
                f"forbidden token(s): {', '.join(science_hits)}",
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor_schema": self.descriptor_schema,
            "adapter_id": self.adapter_id,
            "version": self.version,
            "kind": self.kind,
            "supported_schema": list(self.supported_schema),
            "environment": list(self.environment),
            "resource_limits": self.resource_limits.to_dict(),
            "verification_fixture": self.verification_fixture,
            "capabilities": sorted(self.capabilities),
            "trust_level": self.trust_level,
            "entrypoint": self.entrypoint.to_dict() if self.entrypoint else None,
            "description": self.description,
            "claims_process_isolation": self.claims_process_isolation,
        }

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> AdapterDescriptor:
        if not isinstance(document, Mapping):
            raise AdapterContractError(
                "invalid_descriptor",
                "adapter descriptor must be a JSON object",
            )
        _reject_unknown(document, _DESCRIPTOR_FIELDS, "descriptor")
        capabilities = document.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) for item in capabilities
        ):
            raise AdapterContractError(
                "invalid_capabilities",
                "capabilities must be a non-empty string array",
            )
        schema_values = document.get("supported_schema")
        if not isinstance(schema_values, list) or not all(
            isinstance(item, str) for item in schema_values
        ):
            raise AdapterContractError(
                "invalid_supported_schema",
                "supported_schema must be a string array",
            )
        environment = document.get("environment", [])
        if not isinstance(environment, list) or not all(
            isinstance(item, str) for item in environment
        ):
            raise AdapterContractError(
                "invalid_environment_requirement",
                "environment must be a string array",
            )
        raw_limits = document.get("resource_limits", {})
        if not isinstance(raw_limits, Mapping):
            raise AdapterContractError(
                "invalid_resource_limit",
                "resource_limits must be an object",
            )
        raw_entrypoint = document.get("entrypoint")
        if raw_entrypoint is not None and not isinstance(raw_entrypoint, Mapping):
            raise AdapterContractError(
                "invalid_entrypoint",
                "entrypoint must be an object when present",
            )
        adapter_id = document.get("adapter_id")
        version = document.get("version")
        kind = document.get("kind")
        fixture = document.get("verification_fixture")
        trust_level = document.get("trust_level", TrustLevel.DISCOVER_ONLY)
        description = document.get("description", "")
        claims_isolation = document.get("claims_process_isolation", False)
        if not isinstance(adapter_id, str) or not isinstance(version, str):
            raise AdapterContractError(
                "invalid_descriptor",
                "adapter_id and version must be strings",
            )
        if not isinstance(kind, str) or not isinstance(fixture, str):
            raise AdapterContractError(
                "invalid_descriptor",
                "kind and verification_fixture must be strings",
            )
        if not isinstance(trust_level, str) or not isinstance(description, str):
            raise AdapterContractError(
                "invalid_descriptor",
                "trust_level and description must be strings",
            )
        if not isinstance(claims_isolation, bool):
            raise AdapterContractError(
                "invalid_descriptor",
                "claims_process_isolation must be a boolean",
            )
        descriptor_schema = document.get("descriptor_schema", ADAPTER_SCHEMA)
        if not isinstance(descriptor_schema, str):
            raise AdapterContractError(
                "invalid_descriptor",
                "descriptor_schema must be a string",
            )
        return cls(
            adapter_id=adapter_id,
            version=version,
            kind=kind,
            supported_schema=tuple(schema_values),
            environment=tuple(environment),
            resource_limits=AdapterResourceLimits.from_dict(raw_limits),
            verification_fixture=fixture,
            capabilities=frozenset(capabilities),
            trust_level=trust_level,
            entrypoint=(
                AdapterEntrypoint.from_dict(raw_entrypoint) if raw_entrypoint is not None else None
            ),
            description=description,
            claims_process_isolation=claims_isolation,
            descriptor_schema=descriptor_schema,
        )


def canonical_descriptor_payload(descriptor: AdapterDescriptor) -> dict[str, Any]:
    """Return the canonical payload hashed for the descriptor digest."""

    return descriptor.to_dict()


def descriptor_digest(descriptor: AdapterDescriptor) -> str:
    """Stable SHA-256 over the canonical JSON descriptor."""

    payload = json.dumps(
        canonical_descriptor_payload(descriptor),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def builtin_adapter_descriptors() -> tuple[AdapterDescriptor, ...]:
    """Describe the existing Local/LSF/storage/transfer/identity capabilities.

    These descriptors are metadata about code that already exists.  They do not
    introduce a second scheduler, controller, storage backend or identity
    authority; they let the registry and evidence matrix name the existing
    capabilities consistently with external adapters.
    """

    common_limits = AdapterResourceLimits(
        max_cpu_seconds=86400,
        max_memory_bytes=64 * 1024 * 1024 * 1024,
        max_concurrency=32,
        max_io_bytes=16 * 1024 * 1024 * 1024,
    )
    return (
        AdapterDescriptor(
            adapter_id="platform.scheduler.local",
            version="1.0.0",
            kind=AdapterKind.SCHEDULER,
            supported_schema=(ADAPTER_SCHEMA,),
            resource_limits=common_limits,
            verification_fixture="tests/test_services_isolation.py",
            capabilities=frozenset({"scheduler.local"}),
            trust_level=TrustLevel.TRUSTED_EXPLICIT,
            description="Existing Local process-group scheduler path (unchanged).",
        ),
        AdapterDescriptor(
            adapter_id="platform.scheduler.lsf",
            version="1.0.0",
            kind=AdapterKind.SCHEDULER,
            supported_schema=(ADAPTER_SCHEMA,),
            resource_limits=common_limits,
            verification_fixture="tests/test_acp2_runtime_lsf_scenario.py",
            capabilities=frozenset({"scheduler.lsf"}),
            trust_level=TrustLevel.TRUSTED_EXPLICIT,
            description="Existing LSF ssh/bsub/bjobs path (unchanged); live run environment-gated.",
        ),
        AdapterDescriptor(
            adapter_id="platform.storage.local",
            version="1.0.0",
            kind=AdapterKind.STORAGE,
            supported_schema=(ADAPTER_SCHEMA,),
            resource_limits=common_limits,
            verification_fixture="tests/test_services_read_root.py",
            capabilities=frozenset({"storage.local"}),
            trust_level=TrustLevel.TRUSTED_EXPLICIT,
            description="Existing local run-root storage binding.",
        ),
        AdapterDescriptor(
            adapter_id="platform.transfer.local",
            version="1.0.0",
            kind=AdapterKind.TRANSFER,
            supported_schema=(ADAPTER_SCHEMA,),
            resource_limits=common_limits,
            verification_fixture="tests/test_services_transfer_verify.py",
            capabilities=frozenset({"transfer.local"}),
            trust_level=TrustLevel.TRUSTED_EXPLICIT,
            description="Existing local transfer/staging path.",
        ),
        AdapterDescriptor(
            adapter_id="platform.identity.local",
            version="1.0.0",
            kind=AdapterKind.IDENTITY,
            supported_schema=(ADAPTER_SCHEMA,),
            resource_limits=common_limits,
            verification_fixture="tests/test_services_identity_lifecycle.py",
            capabilities=frozenset({"identity.local"}),
            trust_level=TrustLevel.TRUSTED_EXPLICIT,
            description="Existing local-single identity/key path.",
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reject_unknown(
    document: Mapping[str, Any],
    allowed: set[str] | frozenset[str],
    label: str,
) -> None:
    unknown = sorted(set(document) - set(allowed))
    if unknown:
        raise AdapterContractError(
            "unknown_metadata_field",
            f"{label} contains unsupported field(s): {', '.join(unknown)}",
        )


def _SCIENCE_HAYSTACK(descriptor: AdapterDescriptor) -> set[str]:  # noqa: N802
    parts: list[str] = [
        descriptor.adapter_id,
        descriptor.kind,
        descriptor.verification_fixture,
        *descriptor.supported_schema,
        *descriptor.capabilities,
    ]
    if descriptor.entrypoint is not None:
        parts.extend([descriptor.entrypoint.module, descriptor.entrypoint.attribute])
    tokens: set[str] = set()
    for part in parts:
        for token in re.split(r"[^a-z0-9]+", part.lower()):
            if token:
                tokens.add(token)
    return tokens
