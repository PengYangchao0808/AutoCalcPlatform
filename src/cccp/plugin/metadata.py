"""Frozen plugin metadata contract for the CCCP trusted-plugin pilot (plan T44).

Discovery reads **metadata only**; nothing here launches a program, imports a
plugin module, or creates a second execution station.  The contract exists
even when no external plugin is installed: :class:`BuiltinIdentity` plus an
explicit empty plugin set is a valid, serialisable catalog (see
:func:`cccp.plugin.discovery.builtin_catalog`).

The built-in task layer (``cccp.calculation``) remains the single execution
station.  A ``pure_analysis`` capability is a read-only seam, so it declares
``requires_subprocess=False`` and can never execute a QC program.  A
``compute`` capability is a *reference* to exactly one existing task kind; it
is never executed by the plugin host.

ACP2 science extension (A-21a) adds, inside this same manifest module:

* capability kind (``compute`` / ``pure_analysis``) and implementation id;
* recipe and validator references;
* the science-contract version range and the entry-point group version;
* the distribution digest and verified fixture evidence;
* an explicit trust level (lowest by default, never inferred);
* a declared-license policy that fails closed on empty or unknown licences.

Old manifests remain readable through :meth:`PluginMetadata.from_dict`; the
adapter supplies the conservative defaults (``untrusted``, no digest, no
fixture evidence) and never grants execution or verification eligibility to
unknown historical fields.

Author: QCcalc Team
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from cccp.calculation.capabilities.contracts import TASK_KINDS, CapabilityKind
from cccp.plugin.errors import PluginLicenseError, PluginMetadataError
from cccp.version import __version__

PLUGIN_API_VERSION = 1
PLUGIN_SCHEMA_VERSION = 1
PLUGIN_API_COMPATIBILITY = ">=1,<2"
BUILTIN_EXECUTION_STATION = "cccp.calculation"

#: ACP2 science contract the plugin manifest declares compatibility with.
PLUGIN_SCIENCE_CONTRACT = "acp2.science"
PLUGIN_SCIENCE_VERSION = 1
PLUGIN_SCIENCE_COMPATIBILITY = ">=1,<2"

#: Stable entry-point group for external science plugins (version 1).
SCIENCE_PLUGIN_ENTRY_POINT_GROUP = "cccp.science_plugins"
SCIENCE_PLUGIN_ENTRY_POINT_VERSION = 1
#: Per-distribution metadata-only manifest read through
#: ``importlib.metadata.Distribution.read_text``.  Reading it never imports
#: plugin code; the entry point itself is only loaded after explicit
#: enablement.
SCIENCE_PLUGIN_MANIFEST_FILENAME = "cccp_science_plugin.json"

#: Licences the platform accepts by default.  SPDX identifiers are
#: case-sensitive.  An empty or unknown licence fails closed; the platform
#: owner may narrow this set with :meth:`PluginLicensePolicy.narrowed`.
DEFAULT_ACCEPTED_LICENSES: frozenset[str] = frozenset(
    {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "LGPL-2.1-only",
        "LGPL-3.0-only",
        "GPL-2.0-only",
        "GPL-3.0-only",
        "AGPL-3.0-only",
        "Unlicense",
        "CC0-1.0",
    }
)

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")
_DOTTED_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_RANGE_TOKEN_RE = re.compile(r"^(>=|<=|==|>|<)\s*(\d+)$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RECIPE_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_VALIDATOR_ID_RE = re.compile(r"^(?:check|software|human)\.[a-z0-9][a-z0-9_.-]*$")
_VALIDATOR_VERSION_RE = re.compile(r"^[0-9][0-9A-Za-z.+-]*$")
_SAFE_TEXT_RE = re.compile(r"\S")


def api_version_in_range(version: int, expression: str) -> bool:
    """Return whether integer *version* satisfies a comma-separated range.

    Supported tokens are ``>=N``, ``>N``, ``<=N``, ``<N`` and ``==N``.  An
    empty or malformed expression never matches (fail closed).
    """
    tokens = [token.strip() for token in expression.split(",") if token.strip()]
    if not tokens:
        return False
    for token in tokens:
        match = _RANGE_TOKEN_RE.match(token)
        if match is None:
            return False
        operator, raw_bound = match.groups()
        bound = int(raw_bound)
        if operator == ">=" and not version >= bound:
            return False
        if operator == ">" and not version > bound:
            return False
        if operator == "<=" and not version <= bound:
            return False
        if operator == "<" and not version < bound:
            return False
        if operator == "==" and not version == bound:
            return False
    return True


def _range_expression_is_well_formed(expression: str) -> bool:
    tokens = [token.strip() for token in expression.split(",") if token.strip()]
    return bool(tokens) and all(_RANGE_TOKEN_RE.match(token) for token in tokens)


def _coerce_mapping(value: object, *, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PluginMetadataError(f"{field_name} must be a mapping")
    return value


def _coerce_sequence(value: object, *, field_name: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise PluginMetadataError(f"{field_name} must be a list")
    return tuple(value)


def _optional_digest(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PluginMetadataError(f"{field_name} must be 'sha256:<64 lowercase hex>' or null")
    digest = value.strip()
    if _DIGEST_RE.fullmatch(digest) is None:
        raise PluginMetadataError(f"{field_name} must be 'sha256:<64 lowercase hex>'")
    return digest


def _required_text(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PluginMetadataError(f"{field_name} must be a non-empty string")
    return value.strip()


def _flag(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise PluginMetadataError(f"{field_name} must be a boolean")
    return value


class PluginTrustLevel(str, Enum):
    """Declared trust level of a plugin manifest.

    ``untrusted`` is the lowest level and the default.  ``trusted`` is never
    inferred from installation or API compatibility; it requires at least one
    :class:`PluginFixtureEvidence` record carrying a real sha256 digest of a
    verified fixture.  ``validated`` and ``verified`` are accepted spelling
    aliases for ``trusted`` and serialise as ``trusted``.
    """

    UNTRUSTED = "untrusted"
    DECLARED = "declared"
    TRUSTED = "trusted"

    @classmethod
    def lowest(cls) -> PluginTrustLevel:
        """Return the default (lowest) trust level."""
        return cls.UNTRUSTED


_TRUST_ALIASES: Mapping[str, PluginTrustLevel] = {
    "validated": PluginTrustLevel.TRUSTED,
    "verified": PluginTrustLevel.TRUSTED,
}


def _coerce_trust_level(value: object) -> PluginTrustLevel:
    if isinstance(value, PluginTrustLevel):
        return value
    if not isinstance(value, str) or not value.strip():
        raise PluginMetadataError("trust_level must not be empty")
    text = value.strip().lower()
    alias = _TRUST_ALIASES.get(text)
    if alias is not None:
        return alias
    try:
        return PluginTrustLevel(text)
    except ValueError as exc:
        allowed = ", ".join(level.value for level in PluginTrustLevel)
        raise PluginMetadataError(
            f"trust_level {value!r} is unknown; expected one of: {allowed}"
        ) from exc


@dataclass(frozen=True, slots=True)
class PluginLicensePolicy:
    """Explicit accepted-license policy for plugin manifests.

    The policy is closed by default: an empty licence or an identifier that is
    not in :attr:`accepted_licenses` raises :class:`PluginLicenseError`.
    ``unknown_policy`` is frozen to ``reject`` so a future manifest cannot
    silently opt into a permissive mode.
    """

    accepted_licenses: frozenset[str] = field(default_factory=lambda: DEFAULT_ACCEPTED_LICENSES)
    unknown_policy: str = "reject"

    def __post_init__(self) -> None:
        if not isinstance(self.accepted_licenses, frozenset):
            object.__setattr__(self, "accepted_licenses", frozenset(self.accepted_licenses))
        if not self.accepted_licenses:
            raise PluginMetadataError("license_policy must accept at least one licence")
        for licence in self.accepted_licenses:
            if not isinstance(licence, str) or not licence.strip():
                raise PluginMetadataError("license_policy entries must be non-empty strings")
        if self.unknown_policy != "reject":
            raise PluginMetadataError("license_policy.unknown_policy must be 'reject'")

    @classmethod
    def default(cls) -> PluginLicensePolicy:
        """Return the platform default policy."""
        return cls()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> PluginLicensePolicy:
        """Build a policy from its serialised form (or return the default)."""
        if payload is None:
            return cls.default()
        data = _coerce_mapping(payload, field_name="license_policy")
        accepted = data.get("accepted_licenses", data.get("accepted"))
        policy = data.get("unknown_policy", data.get("unknown", "reject"))
        if accepted is None:
            return cls(unknown_policy=str(policy))
        return cls(
            accepted_licenses=frozenset(
                str(item)
                for item in _coerce_sequence(
                    accepted, field_name="license_policy.accepted_licenses"
                )
            ),
            unknown_policy=str(policy),
        )

    def narrowed(self, *, excluded: Iterable[str] = ()) -> PluginLicensePolicy:
        """Return a policy with *excluded* identifiers removed.

        Only narrowing is supported: the platform owner can accept fewer
        licences, never silently widen the default set through this method.
        """
        removed = frozenset(str(item) for item in excluded)
        remaining = self.accepted_licenses - removed
        if not remaining:
            raise PluginMetadataError("narrowing the license policy would accept nothing")
        return PluginLicensePolicy(accepted_licenses=remaining, unknown_policy=self.unknown_policy)

    def validate(self, licence: object, *, plugin: str | None = None) -> str:
        """Return the accepted licence identifier or raise the typed error.

        Raises:
            PluginLicenseError: The licence is empty, not a string, or not in
                the accepted set.
        """
        if not isinstance(licence, str) or not licence.strip():
            raise PluginLicenseError(
                "" if licence is None else str(licence),
                plugin=plugin,
                reason="license must be declared (empty is not allowed)",
            )
        value = licence.strip()
        if value not in self.accepted_licenses:
            raise PluginLicenseError(
                value,
                plugin=plugin,
                reason=(
                    "license is not in the accepted-license policy "
                    f"({', '.join(sorted(self.accepted_licenses))})"
                ),
            )
        return value

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-safe policy description."""
        return {
            "accepted_licenses": sorted(self.accepted_licenses),
            "unknown_policy": self.unknown_policy,
        }


@dataclass(frozen=True, slots=True)
class PluginFixtureEvidence:
    """Verified fixture evidence backing a ``trusted`` manifest.

    ``digest`` must be a real ``sha256:<64 lowercase hex>`` value.  The
    evidence is declarative: the platform owner verifies the fixture itself;
    the manifest can only carry the claim, never promote itself.
    """

    fixture_id: str
    digest: str
    verified: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        fixture_id = _required_text(self.fixture_id, field_name="fixture_evidence.fixture_id")
        if _RECIPE_ID_RE.fullmatch(fixture_id) is None:
            raise PluginMetadataError("fixture_evidence.fixture_id must match ^[a-z][a-z0-9_.-]*$")
        digest = _optional_digest(self.digest, field_name="fixture_evidence.digest")
        if digest is None:
            raise PluginMetadataError("fixture_evidence.digest must not be null")
        _flag(self.verified, field_name="fixture_evidence.verified")
        if not isinstance(self.description, str):
            raise PluginMetadataError("fixture_evidence.description must be a string")
        object.__setattr__(self, "fixture_id", fixture_id)
        object.__setattr__(self, "digest", digest)
        object.__setattr__(self, "description", self.description.strip())

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PluginFixtureEvidence:
        """Build one evidence record from its serialised form."""
        data = _coerce_mapping(payload, field_name="fixture_evidence")
        return cls(
            fixture_id=str(data.get("fixture_id", "")),
            digest=str(data.get("digest", "")),
            verified=data.get("verified", True),
            description=str(data.get("description", "")),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe evidence record."""
        return {
            "fixture_id": self.fixture_id,
            "digest": self.digest,
            "verified": self.verified,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class PluginRecipeRef:
    """A versioned recipe reference declared by one plugin capability."""

    recipe_id: str
    schema_version: int = 1
    digest: str | None = None

    def __post_init__(self) -> None:
        recipe_id = _required_text(self.recipe_id, field_name="recipe_ref.recipe_id")
        if _RECIPE_ID_RE.fullmatch(recipe_id) is None:
            raise PluginMetadataError("recipe_ref.recipe_id must match ^[a-z][a-z0-9_.-]*$")
        if not isinstance(self.schema_version, int) or self.schema_version < 1:
            raise PluginMetadataError("recipe_ref.schema_version must be >= 1")
        digest = _optional_digest(self.digest, field_name="recipe_ref.digest")
        object.__setattr__(self, "recipe_id", recipe_id)
        object.__setattr__(self, "digest", digest)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PluginRecipeRef:
        """Build a recipe reference from its serialised form."""
        data = _coerce_mapping(payload, field_name="recipe_ref")
        return cls(
            recipe_id=str(data.get("recipe_id", "")),
            schema_version=data.get("schema_version", 1),
            digest=data.get("digest"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe recipe reference."""
        return {
            "recipe_id": self.recipe_id,
            "schema_version": self.schema_version,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class PluginValidatorRef:
    """A versioned validator reference declared by one plugin capability.

    The identifier vocabulary matches ``recipe.schema.json``: validation
    references start with ``check.``, ``software.`` or ``human.``.
    """

    validator_id: str
    validator_version: str = "1"
    required: bool = True

    def __post_init__(self) -> None:
        validator_id = _required_text(self.validator_id, field_name="validator_ref.validator_id")
        if _VALIDATOR_ID_RE.fullmatch(validator_id) is None:
            raise PluginMetadataError(
                "validator_ref.validator_id must start with check./software./human."
            )
        version = _required_text(
            self.validator_version, field_name="validator_ref.validator_version"
        )
        if _VALIDATOR_VERSION_RE.fullmatch(version) is None:
            raise PluginMetadataError("validator_ref.validator_version must be a version string")
        _flag(self.required, field_name="validator_ref.required")
        object.__setattr__(self, "validator_id", validator_id)
        object.__setattr__(self, "validator_version", version)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PluginValidatorRef:
        """Build a validator reference from its serialised form."""
        data = _coerce_mapping(payload, field_name="validator_ref")
        return cls(
            validator_id=str(data.get("validator_id", "")),
            validator_version=str(data.get("validator_version", "1")),
            required=data.get("required", True),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe validator reference."""
        return {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class ApiCompatibility:
    """API contract a plugin was built against.

    ``api_version`` is the version the plugin targets, ``compatible_range``
    is the range it accepts from the host, and ``schema_version`` versions
    the metadata record itself.
    """

    api_version: int = PLUGIN_API_VERSION
    compatible_range: str = PLUGIN_API_COMPATIBILITY
    schema_version: int = PLUGIN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.api_version < 1:
            raise PluginMetadataError("api.api_version must be >= 1")
        if self.schema_version < 1:
            raise PluginMetadataError("api.schema_version must be >= 1")
        if not api_version_in_range(self.api_version, f"=={self.api_version}"):
            raise PluginMetadataError("api.api_version must be a positive integer")
        if not self.compatible_range.strip():
            raise PluginMetadataError("api.compatible_range must not be empty")
        if not _range_expression_is_well_formed(self.compatible_range):
            raise PluginMetadataError("api.compatible_range must be a valid version range")

    def supports_host_api(self, host_api_version: int = PLUGIN_API_VERSION) -> bool:
        """Return whether *host_api_version* falls in the declared range."""
        return api_version_in_range(host_api_version, self.compatible_range)


@dataclass(frozen=True, slots=True)
class ScienceSoftwareVersion:
    """Science-software version window the plugin was validated against.

    ``verified`` is the tri-state evidence marker: ``False`` means the range
    is declared but has not been confirmed with the real binary in this
    environment (recorded as NOT_VERIFIED, never as a pass).
    """

    software: str
    version_range: str
    verified: bool = False

    def __post_init__(self) -> None:
        if not self.software.strip():
            raise PluginMetadataError("science_software.software must not be empty")
        if not self.version_range.strip():
            raise PluginMetadataError("science_software.version_range must not be empty")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ScienceSoftwareVersion:
        """Build a science-software window from its serialised form."""
        data = _coerce_mapping(payload, field_name="science_software")
        return cls(
            software=str(data.get("software", "")),
            version_range=str(data.get("version_range", "")),
            verified=data.get("verified", False),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe version window."""
        return {
            "software": self.software,
            "version_range": self.version_range,
            "verified": self.verified,
        }


@dataclass(frozen=True, slots=True)
class PluginCapability:
    """One capability the plugin declares, with the schema it emits.

    ``kind`` separates a ``compute`` capability (a reference to exactly one
    existing :data:`cccp.calculation.capabilities.contracts.TASK_KINDS` entry,
    executed only by the built-in task layer) from a ``pure_analysis``
    capability (read-only, no subprocess).  ``implementation_id`` is the
    dotted implementation identity from the capability contract.  Recipe and
    validator references are declarations only; they grant no execution.
    """

    name: str
    schema: str
    schema_version: int = PLUGIN_SCHEMA_VERSION
    kind: CapabilityKind | str = CapabilityKind.PURE_ANALYSIS
    implementation_id: str = ""
    delegates_to_task_kind: str | None = None
    recipe_refs: tuple[PluginRecipeRef, ...] = ()
    validator_refs: tuple[PluginValidatorRef, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise PluginMetadataError("capability.name must not be empty")
        if not self.schema.strip():
            raise PluginMetadataError("capability.schema must not be empty")
        if self.schema_version < 1:
            raise PluginMetadataError("capability.schema_version must be >= 1")
        kind: CapabilityKind
        if isinstance(self.kind, CapabilityKind):
            kind = self.kind
        else:
            try:
                kind = CapabilityKind(self.kind)
            except ValueError as exc:
                allowed = ", ".join(item.value for item in CapabilityKind)
                raise PluginMetadataError(
                    f"capability.kind {self.kind!r} is unknown; expected one of: {allowed}"
                ) from exc
        if self.delegates_to_task_kind is not None:
            if not isinstance(self.delegates_to_task_kind, str) or not self.delegates_to_task_kind:
                raise PluginMetadataError(
                    "capability.delegates_to_task_kind must be null or a string"
                )
            if self.delegates_to_task_kind not in TASK_KINDS:
                raise PluginMetadataError(
                    "capability.delegates_to_task_kind must be one of the "
                    "fifteen existing task kinds"
                )
        if kind is CapabilityKind.COMPUTE:
            if self.delegates_to_task_kind is None:
                raise PluginMetadataError(
                    "a compute capability must delegate to exactly one existing task kind"
                )
        elif self.delegates_to_task_kind is not None:
            raise PluginMetadataError("a pure_analysis capability must not delegate to a task kind")
        implementation_id = self.implementation_id.strip()
        if implementation_id and _DOTTED_ID_RE.fullmatch(implementation_id) is None:
            raise PluginMetadataError(
                "capability.implementation_id must be a dotted identifier "
                "(e.g. 'vendor.science.validator')"
            )
        recipe_refs = tuple(self.recipe_refs)
        validator_refs = tuple(self.validator_refs)
        for ref in recipe_refs:
            if not isinstance(ref, PluginRecipeRef):
                raise PluginMetadataError("capability.recipe_refs must contain PluginRecipeRef")
        for ref in validator_refs:
            if not isinstance(ref, PluginValidatorRef):
                raise PluginMetadataError(
                    "capability.validator_refs must contain PluginValidatorRef"
                )
        if len({(ref.recipe_id, ref.schema_version) for ref in recipe_refs}) != len(recipe_refs):
            raise PluginMetadataError("capability recipe references must be unique")
        if len({(ref.validator_id, ref.validator_version) for ref in validator_refs}) != len(
            validator_refs
        ):
            raise PluginMetadataError("capability validator references must be unique")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "implementation_id", implementation_id)
        object.__setattr__(self, "recipe_refs", recipe_refs)
        object.__setattr__(self, "validator_refs", validator_refs)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> PluginCapability:
        """Build a capability from its serialised form (legacy-safe)."""
        data = _coerce_mapping(payload, field_name="capability")
        recipe_refs = tuple(
            PluginRecipeRef.from_dict(item)
            if isinstance(item, Mapping)
            else PluginRecipeRef(recipe_id=str(item))
            for item in _coerce_sequence(data.get("recipe_refs", ()), field_name="recipe_refs")
        )
        validator_refs = tuple(
            PluginValidatorRef.from_dict(item)
            if isinstance(item, Mapping)
            else PluginValidatorRef(validator_id=str(item))
            for item in _coerce_sequence(
                data.get("validator_refs", ()), field_name="validator_refs"
            )
        )
        return cls(
            name=str(data.get("name", "")),
            schema=str(data.get("schema", "")),
            schema_version=data.get("schema_version", PLUGIN_SCHEMA_VERSION),
            kind=data.get("kind", CapabilityKind.PURE_ANALYSIS),
            implementation_id=str(data.get("implementation_id", "")),
            delegates_to_task_kind=data.get("delegates_to_task_kind"),
            recipe_refs=recipe_refs,
            validator_refs=validator_refs,
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe capability declaration."""
        return {
            "name": self.name,
            "schema": self.schema,
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "implementation_id": self.implementation_id,
            "delegates_to_task_kind": self.delegates_to_task_kind,
            "recipe_refs": [ref.to_dict() for ref in self.recipe_refs],
            "validator_refs": [ref.to_dict() for ref in self.validator_refs],
        }


@dataclass(frozen=True, slots=True)
class ResourceRequirements:
    """Declared resource needs; used for reporting, never auto-granted."""

    requires_subprocess: bool = False
    requires_network: bool = False
    min_memory_mb: int | None = None
    cpu_cores: int | None = None

    def __post_init__(self) -> None:
        if self.min_memory_mb is not None and self.min_memory_mb < 0:
            raise PluginMetadataError("resources.min_memory_mb must be >= 0")
        if self.cpu_cores is not None and self.cpu_cores < 1:
            raise PluginMetadataError("resources.cpu_cores must be >= 1")

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ResourceRequirements:
        """Build resource requirements from their serialised form."""
        if payload is None:
            return cls()
        data = _coerce_mapping(payload, field_name="resources")
        return cls(
            requires_subprocess=data.get("requires_subprocess", False),
            requires_network=data.get("requires_network", False),
            min_memory_mb=data.get("min_memory_mb"),
            cpu_cores=data.get("cpu_cores"),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe resource declaration."""
        return {
            "requires_subprocess": self.requires_subprocess,
            "requires_network": self.requires_network,
            "min_memory_mb": self.min_memory_mb,
            "cpu_cores": self.cpu_cores,
        }


@dataclass(frozen=True, slots=True)
class ErrorSemantics:
    """Frozen failure contract a plugin must honour.

    The host refuses metadata that claims it may override a built-in
    capability; override is not a supported plugin behaviour.
    """

    pre_launch_typed: bool = True
    partial_output_tolerant: bool = True
    never_overrides_builtin: bool = True

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ErrorSemantics:
        """Build the failure contract from its serialised form."""
        if payload is None:
            return cls()
        data = _coerce_mapping(payload, field_name="error_semantics")
        return cls(
            pre_launch_typed=data.get("pre_launch_typed", True),
            partial_output_tolerant=data.get("partial_output_tolerant", True),
            never_overrides_builtin=data.get("never_overrides_builtin", True),
        )

    def to_dict(self) -> dict[str, object]:
        """Return the JSON-safe failure contract."""
        return {
            "pre_launch_typed": self.pre_launch_typed,
            "partial_output_tolerant": self.partial_output_tolerant,
            "never_overrides_builtin": self.never_overrides_builtin,
        }


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    """The complete, frozen discovery record for one plugin.

    Every field required by plan T44 is present and non-optional except the
    explicitly-optional lists/maps.  The record is pure data: constructing it
    never imports the plugin and never runs its loader.  The ACP2 science
    extension keeps conservative defaults so a legacy manifest loaded through
    :meth:`from_dict` is ``untrusted`` and carries no evidence.
    """

    name: str
    distribution: str
    version: str
    api: ApiCompatibility = field(default_factory=ApiCompatibility)
    capabilities: tuple[PluginCapability, ...] = ()
    science_software: tuple[ScienceSoftwareVersion, ...] = ()
    resources: ResourceRequirements = field(default_factory=ResourceRequirements)
    license: str = ""
    dependencies: tuple[str, ...] = ()
    error_semantics: ErrorSemantics = field(default_factory=ErrorSemantics)
    default_enabled: bool = False
    science_version_range: str = PLUGIN_SCIENCE_COMPATIBILITY
    entry_point_group_version: int = SCIENCE_PLUGIN_ENTRY_POINT_VERSION
    distribution_digest: str | None = None
    fixture_evidence: tuple[PluginFixtureEvidence, ...] = ()
    trust_level: PluginTrustLevel | str = PluginTrustLevel.UNTRUSTED
    license_policy: PluginLicensePolicy = field(default_factory=PluginLicensePolicy.default)

    def __post_init__(self) -> None:
        if _NAME_RE.match(self.name) is None:
            raise PluginMetadataError(
                f"plugin name {self.name!r} must be dotted lowercase "
                "(e.g. 'vendor.readonly_parser')"
            )
        if not self.distribution.strip():
            raise PluginMetadataError("distribution must not be empty")
        if not self.version.strip():
            raise PluginMetadataError("version must not be empty")
        trust_level = _coerce_trust_level(self.trust_level)
        if not isinstance(self.license_policy, PluginLicensePolicy):
            raise PluginMetadataError("license_policy must be a PluginLicensePolicy")
        licence = self.license_policy.validate(self.license, plugin=self.name)
        if not self.capabilities:
            raise PluginMetadataError("at least one capability must be declared")
        capability_names = [capability.name for capability in self.capabilities]
        if len(set(capability_names)) != len(capability_names):
            raise PluginMetadataError("capability names must be unique")
        if not self.error_semantics.never_overrides_builtin:
            raise PluginMetadataError("error_semantics.never_overrides_builtin must be True")
        if not _range_expression_is_well_formed(self.science_version_range):
            raise PluginMetadataError("science_version_range must be a valid version range")
        if (
            not isinstance(self.entry_point_group_version, int)
            or self.entry_point_group_version < 1
        ):
            raise PluginMetadataError("entry_point_group_version must be >= 1")
        digest = _optional_digest(self.distribution_digest, field_name="distribution_digest")
        fixture_evidence = tuple(self.fixture_evidence)
        for evidence in fixture_evidence:
            if not isinstance(evidence, PluginFixtureEvidence):
                raise PluginMetadataError(
                    "fixture_evidence must contain PluginFixtureEvidence records"
                )
        if len({evidence.fixture_id for evidence in fixture_evidence}) != len(fixture_evidence):
            raise PluginMetadataError("fixture_evidence ids must be unique")
        has_verified_fixture = any(
            evidence.verified and bool(evidence.digest) for evidence in fixture_evidence
        )
        if trust_level is PluginTrustLevel.TRUSTED and not has_verified_fixture:
            raise PluginMetadataError(
                "trust_level 'trusted' requires verified fixture evidence with a sha256 digest"
            )
        pure_analysis_only = all(
            capability.kind is CapabilityKind.PURE_ANALYSIS for capability in self.capabilities
        )
        if pure_analysis_only and self.resources.requires_subprocess:
            raise PluginMetadataError(
                "a pure_analysis-only plugin cannot declare requires_subprocess=True"
            )
        object.__setattr__(self, "dependencies", tuple(str(dep) for dep in self.dependencies))
        object.__setattr__(self, "science_software", tuple(self.science_software))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(self, "license", licence)
        object.__setattr__(self, "trust_level", trust_level)
        object.__setattr__(self, "distribution_digest", digest)
        object.__setattr__(self, "fixture_evidence", fixture_evidence)

    def is_api_compatible(self, host_api_version: int = PLUGIN_API_VERSION) -> bool:
        """Return whether this plugin accepts *host_api_version*."""
        return self.api.supports_host_api(host_api_version)

    def is_science_compatible(self, host_science_version: int = PLUGIN_SCIENCE_VERSION) -> bool:
        """Return whether this plugin accepts the host ACP2 science version."""
        return api_version_in_range(host_science_version, self.science_version_range)

    @property
    def has_verified_fixture_evidence(self) -> bool:
        """Return True when at least one verified fixture digest is declared."""
        return any(
            evidence.verified and bool(evidence.digest) for evidence in self.fixture_evidence
        )

    @property
    def is_trusted(self) -> bool:
        """Return True only for an evidence-backed ``trusted`` manifest."""
        return self.trust_level is PluginTrustLevel.TRUSTED and self.has_verified_fixture_evidence

    def to_dict(self) -> dict[str, object]:
        """Serialise the metadata to a JSON-safe dictionary."""
        return {
            "name": self.name,
            "distribution": self.distribution,
            "version": self.version,
            "api": {
                "api_version": self.api.api_version,
                "compatible_range": self.api.compatible_range,
                "schema_version": self.api.schema_version,
            },
            "capabilities": [capability.to_dict() for capability in self.capabilities],
            "science_software": [entry.to_dict() for entry in self.science_software],
            "resources": self.resources.to_dict(),
            "license": self.license,
            "dependencies": list(self.dependencies),
            "error_semantics": self.error_semantics.to_dict(),
            "default_enabled": self.default_enabled,
            "science_version_range": self.science_version_range,
            "entry_point_group_version": self.entry_point_group_version,
            "distribution_digest": self.distribution_digest,
            "fixture_evidence": [evidence.to_dict() for evidence in self.fixture_evidence],
            "trust_level": self.trust_level.value,
            "license_policy": self.license_policy.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
        *,
        license_policy: PluginLicensePolicy | None = None,
    ) -> PluginMetadata:
        """Build metadata from a legacy or extended manifest mapping.

        Missing A-21 fields receive conservative defaults: ``untrusted``
        trust, no distribution digest, no fixture evidence and the platform
        default licence policy.  Unknown historical fields are ignored and
        never grant trust, verification or execution eligibility.

        Raises:
            PluginMetadataError: A required field is missing or malformed.
            PluginLicenseError: The declared licence is empty or unknown.
        """
        data = _coerce_mapping(payload, field_name="plugin metadata")
        name = data.get("name")
        distribution = data.get("distribution")
        version = data.get("version")
        if (
            not isinstance(name, str)
            or not isinstance(distribution, str)
            or not isinstance(version, str)
        ):
            raise PluginMetadataError("name, distribution and version must be strings")
        api_payload = data.get("api")
        api = (
            ApiCompatibility(
                api_version=int(api_payload.get("api_version", PLUGIN_API_VERSION)),
                compatible_range=str(api_payload.get("compatible_range", PLUGIN_API_COMPATIBILITY)),
                schema_version=int(api_payload.get("schema_version", PLUGIN_SCHEMA_VERSION)),
            )
            if isinstance(api_payload, Mapping)
            else ApiCompatibility()
        )
        capabilities = tuple(
            PluginCapability.from_dict(item)
            for item in _coerce_sequence(data.get("capabilities", ()), field_name="capabilities")
        )
        science_software = tuple(
            ScienceSoftwareVersion.from_dict(item)
            for item in _coerce_sequence(
                data.get("science_software", ()), field_name="science_software"
            )
        )
        fixture_payload = data.get("fixture_evidence", ())
        fixture_evidence = tuple(
            PluginFixtureEvidence.from_dict(item)
            for item in _coerce_sequence(fixture_payload, field_name="fixture_evidence")
        )
        policy = license_policy
        if policy is None:
            raw_policy = data.get("license_policy")
            policy = (
                PluginLicensePolicy.from_dict(raw_policy)
                if isinstance(raw_policy, Mapping)
                else PluginLicensePolicy.default()
            )
        return cls(
            name=name,
            distribution=distribution,
            version=version,
            api=api,
            capabilities=capabilities,
            science_software=science_software,
            resources=ResourceRequirements.from_dict(
                data.get("resources") if isinstance(data.get("resources"), Mapping) else None
            ),
            license=str(data.get("license", "")),
            dependencies=tuple(
                str(dep)
                for dep in _coerce_sequence(data.get("dependencies", ()), field_name="dependencies")
            ),
            error_semantics=ErrorSemantics.from_dict(
                data.get("error_semantics")
                if isinstance(data.get("error_semantics"), Mapping)
                else None
            ),
            default_enabled=data.get("default_enabled", False),
            science_version_range=str(
                data.get("science_version_range", PLUGIN_SCIENCE_COMPATIBILITY)
            ),
            entry_point_group_version=int(
                data.get("entry_point_group_version", SCIENCE_PLUGIN_ENTRY_POINT_VERSION)
            ),
            distribution_digest=data.get("distribution_digest"),
            fixture_evidence=fixture_evidence,
            trust_level=data.get("trust_level", PluginTrustLevel.UNTRUSTED),
            license_policy=policy,
        )


@dataclass(frozen=True, slots=True)
class BuiltinIdentity:
    """Version identity of the host itself.

    Present even with zero external plugins so a catalog always states which
    CCCP/API executed it and which station owns execution.
    """

    distribution: str = "cccp"
    version: str = __version__
    api_compatibility: str = PLUGIN_API_COMPATIBILITY
    execution_station: str = BUILTIN_EXECUTION_STATION

    def to_dict(self) -> dict[str, object]:
        """Serialise the built-in identity to a JSON-safe dictionary."""
        return {
            "distribution": self.distribution,
            "version": self.version,
            "api_compatibility": self.api_compatibility,
            "execution_station": self.execution_station,
        }


__all__ = [
    "BUILTIN_EXECUTION_STATION",
    "DEFAULT_ACCEPTED_LICENSES",
    "PLUGIN_API_COMPATIBILITY",
    "PLUGIN_API_VERSION",
    "PLUGIN_SCHEMA_VERSION",
    "PLUGIN_SCIENCE_COMPATIBILITY",
    "PLUGIN_SCIENCE_CONTRACT",
    "PLUGIN_SCIENCE_VERSION",
    "SCIENCE_PLUGIN_ENTRY_POINT_GROUP",
    "SCIENCE_PLUGIN_ENTRY_POINT_VERSION",
    "SCIENCE_PLUGIN_MANIFEST_FILENAME",
    "ApiCompatibility",
    "BuiltinIdentity",
    "ErrorSemantics",
    "PluginCapability",
    "PluginFixtureEvidence",
    "PluginLicensePolicy",
    "PluginMetadata",
    "PluginRecipeRef",
    "PluginTrustLevel",
    "PluginValidatorRef",
    "ResourceRequirements",
    "ScienceSoftwareVersion",
    "api_version_in_range",
]
