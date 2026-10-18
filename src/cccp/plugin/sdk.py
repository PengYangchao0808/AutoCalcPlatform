"""Plugin SDK for trusted read-only capabilities (plan T45).

The SDK is a thin contract layer above :mod:`cccp.plugin.host`.  It never
creates a second task chain: a capability consumes the built-in
:class:`~cccp.calculation.requests.TaskRequest` envelope, runs with the built-in
:class:`~cccp.calculation.context.TaskContext`, and returns the built-in
:class:`~cccp.calculation.results.TaskResult` envelope.  The built-in task
layer (``cccp.calculation``) remains the only execution station.

On top of the T44 discovery/enable/load boundary the SDK adds:

* **Version/digest locking** — a deterministic sha256 identity over plugin
  version, API/schema range, capability schemas, declared dependencies and
  validated software windows.  The same lock supplies release, step and
  product identity fields (the field *specification* for downstream consumers
  is plan T46's handoff).
* **Dependency availability** — a declared dependency absent from the
  environment is a typed pre-load failure; nothing is installed or imported
  on the plugin's behalf.
* **Result-contract validation** — envelope identity, explicit units,
  atom-order mapping and partial-output semantics.
* **Cooperative cancellation / timeout** — helpers a capability calls between
  units; no thread or subprocess is created by the SDK.
* **Failure reports with rollback guidance** — every typed failure carries an
  explicit operator action.  Silent substitution of a built-in result under a
  plugin identity is never allowed.

Author: QCcalc Team
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from cccp.calculation.context import TaskContext, resolve_context
from cccp.calculation.contracts import JsonObject, Provenance
from cccp.calculation.errors import TaskCancelledError, TaskInputError
from cccp.calculation.products.contracts import (
    CoordinateUnits,
    EnergyUnits,
    FrequencyUnits,
)
from cccp.calculation.requests import (
    MethodSpec,
    StructureInput,
    TaskKind,
    TaskRequest,
    TaskResources,
)
from cccp.calculation.results import (
    TASK_RESULT_SCHEMA_VERSION,
    ErrorKind,
    TaskResult,
)
from cccp.plugin.discovery import BUILTIN_RESERVED_NAMES
from cccp.plugin.errors import (
    PluginCapabilityConflictError,
    PluginContractViolationError,
    PluginDependencyUnavailableError,
    PluginError,
    PluginLoadError,
    PluginMetadataError,
    PluginTimeoutError,
    PluginUnavailableError,
    PluginVersionMismatchError,
)
from cccp.plugin.host import PluginHost
from cccp.plugin.metadata import (
    PLUGIN_API_COMPATIBILITY,
    PLUGIN_API_VERSION,
    PluginCapability,
    PluginMetadata,
    ScienceSoftwareVersion,
)

#: SDK contract version; advances together with the plugin API version.
PLUGIN_SDK_VERSION = PLUGIN_API_VERSION
PLUGIN_SDK_COMPATIBILITY = PLUGIN_API_COMPATIBILITY
PLUGIN_DIGEST_SCHEMA_VERSION = 1
PLUGIN_IDENTITY_SCOPE_VERSION = 1

#: Minor releases during which a deprecated SDK symbol stays available.
SDK_DEPRECATION_WINDOW_MINOR_RELEASES = 1

#: Unit axes the SDK knows how to validate; unknown axes are accepted only as
#: explicit non-empty strings so a new science axis does not require an SDK
#: release, while a typo in a known axis is rejected.
_ALLOWED_UNITS: dict[str, frozenset[str]] = {
    "coordinates": frozenset(unit.value for unit in CoordinateUnits),
    "energy": frozenset(unit.value for unit in EnergyUnits),
    "frequencies": frozenset(unit.value for unit in FrequencyUnits),
    "gradients": frozenset({"hartree/bohr"}),
    "entropy": frozenset({"au"}),
}

_ATOM_MAPPINGS: frozenset[str] = frozenset({"input_order", "atom_uid"})

#: JSON-safe unit spelling is part of the product identity: callers compare
#: these strings, never localized display labels.
UNIT_ANGSTROM = CoordinateUnits.ANGSTROM.value
UNIT_HARTREE = EnergyUnits.HARTREE.value


class PluginIdentityScope(str, Enum):
    """Consumption point of one plugin version digest."""

    RELEASE = "release"
    STEP = "step"
    PRODUCT = "product"


@dataclass(frozen=True, slots=True)
class PluginOutputSpec:
    """Declared output contract of one capability.

    ``units`` maps a scientific axis (``coordinates``, ``energy``,
    ``frequencies``, ...) to its explicit unit.  ``atom_mapping`` is either
    ``input_order`` (the output atom order equals the input symbols) or
    ``atom_uid`` (the output carries a stable, unique atom-uid order).
    ``allows_partial`` records whether a partial result (``complete=False``
    with valid sub-items) is acceptable for this capability.
    """

    units: tuple[tuple[str, str], ...] = ()
    atom_mapping: str = "input_order"
    allows_partial: bool = True

    def __post_init__(self) -> None:
        normalized = tuple((str(axis), str(unit)) for axis, unit in self.units)
        axes = [axis for axis, _unit in normalized]
        if len(set(axes)) != len(axes):
            raise PluginMetadataError("output unit axes must be unique")
        for axis, unit in normalized:
            if not axis.strip() or not unit.strip():
                raise PluginMetadataError("output unit axis and unit must be non-empty")
        if self.atom_mapping not in _ATOM_MAPPINGS:
            raise PluginMetadataError(
                f"atom_mapping must be one of {sorted(_ATOM_MAPPINGS)}, got {self.atom_mapping!r}"
            )
        object.__setattr__(self, "units", normalized)

    def unit_map(self) -> dict[str, str]:
        """Return the declared units as a mutable dict."""
        return dict(self.units)

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "units": {axis: unit for axis, unit in self.units},
            "atom_mapping": self.atom_mapping,
            "allows_partial": self.allows_partial,
        }


@dataclass(frozen=True, slots=True)
class ResolvedDependency:
    """One declared dependency plus the version actually observed.

    ``source`` records how the version was resolved: ``distribution``
    (importlib metadata), ``stdlib`` (Python runtime), or ``module`` (an
    importable module without distribution metadata, recorded as
    ``unversioned``).  There is no ``missing`` state: missing dependencies
    raise :class:`~cccp.plugin.errors.PluginDependencyUnavailableError`.
    """

    name: str
    version: str
    source: str = "distribution"

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {"name": self.name, "version": self.version, "source": self.source}


@dataclass(frozen=True, slots=True)
class PluginVersionDigest:
    """Deterministic plugin identity for release / step / product locking.

    The ``digest`` is sha256 over canonical JSON of every identity-bearing
    metadata field plus the resolved dependency versions.  Changing a plugin
    version, API/schema version, capability schema, dependency version or
    validated software window changes the digest; changing only a display
    label does not.
    """

    name: str
    distribution: str
    plugin_version: str
    api_version: int
    compatible_range: str
    schema_version: int
    capabilities: tuple[PluginCapability, ...]
    declared_dependencies: tuple[str, ...]
    resolved_dependencies: tuple[ResolvedDependency, ...]
    science_software: tuple[ScienceSoftwareVersion, ...]
    digest_schema_version: int = PLUGIN_DIGEST_SCHEMA_VERSION
    digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(
            self,
            "declared_dependencies",
            tuple(str(dependency) for dependency in self.declared_dependencies),
        )
        object.__setattr__(self, "resolved_dependencies", tuple(self.resolved_dependencies))
        object.__setattr__(self, "science_software", tuple(self.science_software))
        if self.digest_schema_version < 1:
            raise PluginMetadataError("digest_schema_version must be >= 1")
        computed = self.compute_digest()
        if self.digest and self.digest != computed:
            raise PluginContractViolationError(
                self.name,
                detail=f"version digest mismatch (declared {self.digest}, computed {computed})",
            )
        object.__setattr__(self, "digest", computed)

    def _digest_payload(self) -> JsonObject:
        return {
            "digest_schema_version": self.digest_schema_version,
            "name": self.name,
            "distribution": self.distribution,
            "plugin_version": self.plugin_version,
            "api_version": self.api_version,
            "compatible_range": self.compatible_range,
            "schema_version": self.schema_version,
            "capabilities": [
                {
                    "name": capability.name,
                    "schema": capability.schema,
                    "schema_version": capability.schema_version,
                }
                for capability in self.capabilities
            ],
            "declared_dependencies": list(self.declared_dependencies),
            "resolved_dependencies": [
                dependency.to_dict() for dependency in self.resolved_dependencies
            ],
            "science_software": [
                {
                    "software": entry.software,
                    "version_range": entry.version_range,
                    "verified": entry.verified,
                }
                for entry in self.science_software
            ],
        }

    def compute_digest(self) -> str:
        """Return sha256 over the canonical JSON identity payload."""
        canonical = json.dumps(
            self._digest_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def capability(self, name: str) -> PluginCapability | None:
        """Return the declared capability record for *name* or ``None``."""
        for capability in self.capabilities:
            if capability.name == name:
                return capability
        return None

    def identity_fields(
        self,
        *,
        scope: PluginIdentityScope,
        capability: str | None = None,
    ) -> JsonObject:
        """Return the fields a release/step/product identity should embed.

        The values are JSON-safe and platform-neutral.  ``PRODUCT`` requires a
        capability because a derived product identity must name the exact
        parser/analysis capability and its schema version.

        Raises:
            PluginContractViolationError: ``PRODUCT`` scope without a
                capability, or an undeclared capability name.
        """
        try:
            scope = PluginIdentityScope(scope)
        except ValueError as exc:
            raise PluginContractViolationError(
                self.name,
                detail=f"unknown identity scope {scope!r}",
            ) from exc
        capability_record: PluginCapability | None = None
        if capability is not None:
            capability_record = self.capability(capability)
            if capability_record is None:
                raise PluginContractViolationError(
                    self.name,
                    detail=f"capability {capability!r} is not declared by the plugin",
                )
        elif scope is PluginIdentityScope.PRODUCT:
            raise PluginContractViolationError(
                self.name,
                detail="PRODUCT identity requires an explicit capability",
            )
        payload: JsonObject = {
            "plugin_identity_scope": scope.value,
            "plugin_identity_scope_version": PLUGIN_IDENTITY_SCOPE_VERSION,
            "plugin_name": self.name,
            "plugin_distribution": self.distribution,
            "plugin_version": self.plugin_version,
            "plugin_api_version": self.api_version,
            "plugin_api_compatibility": self.compatible_range,
            "plugin_schema_version": self.schema_version,
            "plugin_digest": self.digest,
            "plugin_digest_schema_version": self.digest_schema_version,
            "plugin_dependencies": [
                dependency.to_dict() for dependency in self.resolved_dependencies
            ],
        }
        if capability_record is not None:
            payload["plugin_capability"] = capability_record.name
            payload["plugin_capability_schema"] = capability_record.schema
            payload["plugin_capability_schema_version"] = capability_record.schema_version
        return payload

    def to_dict(self) -> JsonObject:
        """Serialise the lock (including the digest) to a JSON-safe dict."""
        payload = self._digest_payload()
        payload["digest"] = self.digest
        return payload


def require_compatible_plugin(
    metadata: PluginMetadata,
    *,
    host_api_version: int = PLUGIN_API_VERSION,
) -> None:
    """Reject a plugin whose declared API range excludes the host.

    Raises:
        PluginVersionMismatchError: The declared range does not include the
            host API version.
    """
    if not metadata.is_api_compatible(host_api_version):
        raise PluginVersionMismatchError(
            metadata.name,
            declared=metadata.api.compatible_range,
            host_api_version=host_api_version,
        )


def resolve_dependency_versions(
    metadata: PluginMetadata,
) -> tuple[ResolvedDependency, ...]:
    """Resolve imported dependency versions without installing or loading code.

    A dependency listed in :attr:`PluginMetadata.dependencies` must be
    importable.  Standard-library modules record the Python runtime version;
    an importable module without distribution metadata records
    ``unversioned`` (the caller can still pin the plugin digest, but should
    prefer distributions for reproducible releases).

    Raises:
        PluginDependencyUnavailableError: One or more declared dependencies
            are not importable.
    """
    resolved: list[ResolvedDependency] = []
    missing: list[str] = []
    seen: set[str] = set()
    for dependency in metadata.dependencies:
        name = dependency.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        spec = _find_spec(name)
        if spec is None:
            missing.append(name)
            continue
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            if name in getattr(sys, "stdlib_module_names", frozenset()):
                resolved.append(
                    ResolvedDependency(
                        name=name,
                        version=f"python-{sys.version_info.major}.{sys.version_info.minor}",
                        source="stdlib",
                    )
                )
            else:
                resolved.append(
                    ResolvedDependency(name=name, version="unversioned", source="module")
                )
        else:
            resolved.append(ResolvedDependency(name=name, version=version, source="distribution"))
    if missing:
        raise PluginDependencyUnavailableError(metadata.name, missing=tuple(missing))
    return tuple(resolved)


def _find_spec(name: str) -> object | None:
    """Return an import spec for *name*, or ``None`` when it is absent."""
    try:
        return importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None


def lock_plugin(
    metadata: PluginMetadata,
    *,
    resolved_dependencies: Mapping[str, str] | None = None,
) -> PluginVersionDigest:
    """Validate compatibility and return the deterministic version lock.

    Args:
        metadata: The frozen plugin metadata record.
        resolved_dependencies: Optional name → version map supplied by a
            controlled installer.  When omitted the environment is inspected
            (no installation, no plugin import).

    Raises:
        PluginVersionMismatchError: The host API is outside the declared range.
        PluginDependencyUnavailableError: A declared dependency is absent.
    """
    require_compatible_plugin(metadata)
    dependencies: tuple[ResolvedDependency, ...]
    if resolved_dependencies is None:
        dependencies = resolve_dependency_versions(metadata)
    else:
        dependencies = tuple(
            ResolvedDependency(name=str(name), version=str(version), source="provided")
            for name, version in sorted(resolved_dependencies.items())
        )
        missing = [
            name
            for name in metadata.dependencies
            if name not in {dependency.name for dependency in dependencies}
        ]
        if missing:
            raise PluginDependencyUnavailableError(metadata.name, missing=tuple(missing))
    return PluginVersionDigest(
        name=metadata.name,
        distribution=metadata.distribution,
        plugin_version=metadata.version,
        api_version=metadata.api.api_version,
        compatible_range=metadata.api.compatible_range,
        schema_version=metadata.api.schema_version,
        capabilities=metadata.capabilities,
        declared_dependencies=metadata.dependencies,
        resolved_dependencies=dependencies,
        science_software=metadata.science_software,
    )


def check_cancelled(context: TaskContext | None) -> None:
    """Raise when the caller requested cooperative cancellation.

    Capabilities call this between units (atoms, frames, records) so a local
    cancel is honoured without killing a process.

    Raises:
        TaskCancelledError: The context's cancellation callback returned True.
    """
    if context is not None and context.is_cancelled():
        raise TaskCancelledError("plugin capability cancelled by caller")


def check_deadline(
    plugin_name: str,
    started_at: float,
    context: TaskContext | None,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Raise when the capability exceeded its cooperative timeout budget.

    ``TaskContext.timeout_s`` is the runtime authority (the caller maps the
    request resource budget into it).  A non-positive budget raises
    immediately; otherwise the check is elapsed-time based and is called
    between units.

    Raises:
        PluginTimeoutError: The elapsed time exceeds the context budget.
    """
    if context is None or context.timeout_s is None:
        return
    timeout_s = float(context.timeout_s)
    if timeout_s <= 0.0 or clock() - started_at > timeout_s:
        raise PluginTimeoutError(plugin_name, timeout_s=timeout_s)


@runtime_checkable
class PluginCapabilityHandler(Protocol):
    """Structural contract a loadable capability object implements."""

    def capability_names(self) -> tuple[str, ...]:
        """Return the capability names this handler serves."""

    def output_spec_for(self, capability: str) -> PluginOutputSpec:
        """Return the declared output contract for *capability*."""

    def invoke(
        self,
        request: TaskRequest,
        *,
        context: TaskContext | None = None,
    ) -> TaskResult:
        """Run the read-only capability and return a built-in result envelope."""


def invoke_capability(
    host: PluginHost,
    plugin_name: str,
    *,
    capability: str,
    request: TaskRequest,
    context: TaskContext | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> TaskResult:
    """Load, enable-check and invoke one declared capability.

    The handler receives the built-in request/context envelopes and must
    return the built-in result envelope.  There is no fallback branch: a
    failure propagates as its typed exception, and the caller decides the
    operator action from :func:`failure_report`.

    Raises:
        PluginUnavailableError: Unknown plugin/capability or no in-process
            loader.
        PluginDisabledError: The plugin was not explicitly enabled.
        PluginVersionMismatchError: The metadata range excludes the host.
        PluginDependencyUnavailableError: A declared dependency is absent.
        PluginCapabilityConflictError: The capability is reserved for a
            built-in task.
        PluginLoadError: The enabled loader raised.
        PluginContractViolationError: The handler shape or result contract is
            invalid.
        PluginTimeoutError: The cooperative timeout budget was exceeded.
        TaskCancelledError: The caller cancelled before/during the call.
    """
    metadata = host.catalog.get(plugin_name)
    if metadata is None:
        raise PluginUnavailableError(plugin_name, reason="not discovered")
    require_compatible_plugin(metadata)
    declared = {entry.name for entry in metadata.capabilities}
    if capability not in declared:
        raise PluginUnavailableError(
            plugin_name, reason=f"capability {capability!r} is not declared"
        )
    if capability in BUILTIN_RESERVED_NAMES or capability in {kind.value for kind in TaskKind}:
        raise PluginCapabilityConflictError(
            plugin_name,
            capability=capability,
            reason="capability name is reserved for a built-in task",
        )
    resolve_dependency_versions(metadata)
    plugin = host.load(plugin_name)
    if not isinstance(plugin, PluginCapabilityHandler):
        raise PluginContractViolationError(
            plugin_name,
            detail="loaded object does not implement PluginCapabilityHandler",
        )
    if capability not in plugin.capability_names():
        raise PluginContractViolationError(
            plugin_name,
            detail=f"handler does not serve declared capability {capability!r}",
        )
    effective_context = resolve_context(request, context)
    check_cancelled(effective_context)
    started_at = clock()
    check_deadline(plugin_name, started_at, effective_context, clock=clock)
    result = plugin.invoke(request, context=effective_context)
    check_deadline(plugin_name, started_at, effective_context, clock=clock)
    if not isinstance(result, TaskResult):
        raise PluginContractViolationError(
            plugin_name,
            detail=f"handler returned {type(result).__name__}, expected TaskResult",
        )
    output_spec = plugin.output_spec_for(capability)
    if not isinstance(output_spec, PluginOutputSpec):
        raise PluginContractViolationError(
            plugin_name,
            detail="output_spec_for() must return PluginOutputSpec",
        )
    validate_result_contract(
        result,
        metadata=metadata,
        capability=capability,
        request=request,
        output_spec=output_spec,
    )
    return result


def validate_result_contract(
    result: TaskResult,
    *,
    metadata: PluginMetadata,
    capability: str,
    request: TaskRequest,
    output_spec: PluginOutputSpec,
) -> None:
    """Validate one plugin result against the declared capability contract.

    Checks (each a :class:`PluginContractViolationError`):

    * the result is the built-in envelope and keeps the request's task;
    * the result schema version is supported;
    * the declared capability and metadata capability agree;
    * partial output (``complete=False``) carries errors, an error kind and
      is allowed by the capability;
    * declared units are present and use the SDK's canonical spelling;
    * atom-order length matches symbols/coordinates and, for
      ``input_order``, equals the input atom order;
    * symbols and coordinates are supplied together.
    """
    name = metadata.name
    if not isinstance(result, TaskResult):
        raise PluginContractViolationError(
            name, detail=f"result must be TaskResult, got {type(result).__name__}"
        )
    if result.task.value != request.task.value:
        raise PluginContractViolationError(
            name,
            detail=(
                f"result task {result.task.value!r} does not match request task "
                f"{request.task.value!r} (a capability never starts a second task chain)"
            ),
        )
    if result.schema_version != TASK_RESULT_SCHEMA_VERSION:
        raise PluginContractViolationError(
            name,
            detail=(
                f"result schema_version {result.schema_version} is not supported "
                f"(expected {TASK_RESULT_SCHEMA_VERSION})"
            ),
        )
    declared = metadata.capabilities
    if capability not in {entry.name for entry in declared}:
        raise PluginContractViolationError(
            name, detail=f"capability {capability!r} is not declared in metadata"
        )
    raw_capability = result.metadata.get("capability")
    if raw_capability != capability:
        raise PluginContractViolationError(
            name,
            detail=(
                f"result metadata capability {raw_capability!r} does not match "
                f"declared capability {capability!r}"
            ),
        )
    if not result.complete:
        if not result.errors:
            raise PluginContractViolationError(
                name, detail="partial result (complete=False) must carry at least one error"
            )
        if result.error_kind is None:
            raise PluginContractViolationError(
                name, detail="partial result (complete=False) must set error_kind"
            )
        if not output_spec.allows_partial:
            raise PluginContractViolationError(
                name, detail=f"capability {capability!r} does not allow partial output"
            )
    elif result.error_kind is not None:
        raise PluginContractViolationError(name, detail="complete result must not carry error_kind")

    _validate_units(name, result, output_spec)
    _validate_atom_mapping(name, result, output_spec)


def _validate_units(name: str, result: TaskResult, output_spec: PluginOutputSpec) -> None:
    raw_units = result.metadata.get("units")
    if not isinstance(raw_units, Mapping):
        raise PluginContractViolationError(
            name, detail="result metadata must carry an explicit 'units' mapping"
        )
    declared_units = output_spec.unit_map()
    for axis, expected in declared_units.items():
        observed = raw_units.get(axis)
        if observed != expected:
            raise PluginContractViolationError(
                name,
                detail=(
                    f"unit for axis {axis!r} is {observed!r}, expected {expected!r} "
                    "(explicit units are part of product identity)"
                ),
            )
    for axis, unit in raw_units.items():
        if not isinstance(axis, str) or not axis.strip():
            raise PluginContractViolationError(name, detail="unit axis must be a non-empty string")
        if not isinstance(unit, str) or not unit.strip():
            raise PluginContractViolationError(
                name, detail=f"unit for axis {axis!r} must be a non-empty string"
            )
        allowed = _ALLOWED_UNITS.get(axis)
        if allowed is not None and unit not in allowed:
            raise PluginContractViolationError(
                name,
                detail=(
                    f"unit {unit!r} for axis {axis!r} is not canonical; "
                    f"expected one of {sorted(allowed)}"
                ),
            )


def _validate_atom_mapping(name: str, result: TaskResult, output_spec: PluginOutputSpec) -> None:
    raw_order = result.metadata.get("atom_order")
    if raw_order is not None:
        if not isinstance(raw_order, (list, tuple)) or any(
            not isinstance(entry, str) for entry in raw_order
        ):
            raise PluginContractViolationError(
                name, detail="result metadata 'atom_order' must be a list of strings"
            )
        atom_order = tuple(raw_order)
    else:
        atom_order = ()
    if (result.symbols is None) != (result.coordinates is None):
        raise PluginContractViolationError(
            name, detail="symbols and coordinates must be supplied together"
        )
    if result.symbols is None or result.coordinates is None:
        return
    if len(result.symbols) != len(result.coordinates):
        raise PluginContractViolationError(
            name,
            detail=(
                f"symbol count {len(result.symbols)} != coordinate row count "
                f"{len(result.coordinates)}"
            ),
        )
    if len(atom_order) != len(result.symbols):
        raise PluginContractViolationError(
            name,
            detail=(f"atom_order length {len(atom_order)} != atom count {len(result.symbols)}"),
        )
    if output_spec.atom_mapping == "input_order":
        if atom_order != tuple(result.symbols):
            raise PluginContractViolationError(
                name,
                detail=(
                    "atom_order does not preserve input atom mapping "
                    f"({atom_order!r} != {tuple(result.symbols)!r})"
                ),
            )
    elif len(set(atom_order)) != len(atom_order):
        raise PluginContractViolationError(name, detail="atom_uid mapping must be unique per atom")


class PluginRollbackAction(str, Enum):
    """Operator action a typed plugin failure requires."""

    REPIN_VERSION = "repin_version"
    FIX_DEPENDENCY = "fix_dependency"
    RESOLVE_CAPABILITY_CONFLICT = "resolve_capability_conflict"
    DISABLE_PLUGIN = "disable_plugin"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class PluginRollbackGuidance:
    """Explicit rollback guidance attached to a plugin failure.

    ``silent_substitution_allowed`` is always ``False``: a failed plugin is
    never transparently replaced by a built-in result under the plugin's
    identity.  Reusing a built-in capability is an explicit operator action.
    """

    action: PluginRollbackAction
    plugin: str
    reason: str
    builtin_capability_preserved: bool = True
    silent_substitution_allowed: bool = False
    requires_operator: bool = True
    retryable: bool = False
    preserve_valid_partial_output: bool = False

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "action": self.action.value,
            "plugin": self.plugin,
            "reason": self.reason,
            "builtin_capability_preserved": self.builtin_capability_preserved,
            "silent_substitution_allowed": self.silent_substitution_allowed,
            "requires_operator": self.requires_operator,
            "retryable": self.retryable,
            "preserve_valid_partial_output": self.preserve_valid_partial_output,
        }


@dataclass(frozen=True, slots=True)
class PluginFailureReport:
    """Typed error plus the explicit rollback decision it implies."""

    error: BaseException
    guidance: PluginRollbackGuidance

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict (no exception object on the wire)."""
        return {
            "error_type": type(self.error).__name__,
            "plugin": self.guidance.plugin,
            "message": str(self.error),
            "rollback": self.guidance.to_dict(),
        }


def failure_report(error: BaseException, *, plugin_name: str | None = None) -> PluginFailureReport:
    """Map a typed failure to a rollback/retry decision.

    Version mismatch is never silently downgraded: the guidance says to
    re-pin the plugin or disable it while the built-in capability stays
    intact.  Missing dependencies are never auto-installed.  Cancellation and
    timeout are retryable control outcomes, not plugin defects.
    """
    name = plugin_name or getattr(error, "name", "") or "<unknown>"
    if isinstance(error, PluginVersionMismatchError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.REPIN_VERSION,
            plugin=name,
            reason=(
                "declared API range excludes this host; re-pin to a compatible "
                "plugin release or disable it explicitly"
            ),
        )
    elif isinstance(error, PluginDependencyUnavailableError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.FIX_DEPENDENCY,
            plugin=name,
            reason=(
                "declared dependency is absent; install it through the controlled "
                "environment or disable the plugin (no automatic installation)"
            ),
        )
    elif isinstance(error, PluginCapabilityConflictError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.RESOLVE_CAPABILITY_CONFLICT,
            plugin=name,
            reason=(
                "capability name collides with a built-in or another plugin; "
                "rename/disable one plugin explicitly"
            ),
        )
    elif isinstance(error, PluginTimeoutError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.RETRY,
            plugin=name,
            reason="cooperative timeout budget exceeded",
            requires_operator=False,
            retryable=True,
        )
    elif isinstance(error, TaskCancelledError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.RETRY,
            plugin=name,
            reason="caller cancelled the capability",
            requires_operator=False,
            retryable=True,
        )
    elif isinstance(error, PluginContractViolationError):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.DISABLE_PLUGIN,
            plugin=name,
            reason=(
                "result violates the declared contract; disable the plugin version "
                "and keep any valid partial output as evidence"
            ),
            preserve_valid_partial_output=True,
        )
    elif isinstance(error, (PluginLoadError, PluginUnavailableError, PluginError)):
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.DISABLE_PLUGIN,
            plugin=name,
            reason="plugin could not be loaded or is unavailable; disable it explicitly",
        )
    else:
        guidance = PluginRollbackGuidance(
            action=PluginRollbackAction.DISABLE_PLUGIN,
            plugin=name,
            reason="unexpected plugin failure; disable it explicitly and inspect the cause",
        )
    return PluginFailureReport(error=error, guidance=guidance)


@dataclass(frozen=True, slots=True)
class PluginSdkDeprecation:
    """One tracked SDK deprecation notice (see the SDK evidence document)."""

    symbol: str
    deprecated_in: str
    removal_not_before: str
    replacement: str
    note: str = ""

    def to_dict(self) -> JsonObject:
        """Serialise to a JSON-safe dict."""
        return {
            "symbol": self.symbol,
            "deprecated_in": self.deprecated_in,
            "removal_not_before": self.removal_not_before,
            "replacement": self.replacement,
            "note": self.note,
        }


#: Empty until the first SDK deprecation.  Kept in code so a deprecation is a
#: reviewable data change, not only a prose edit.
SDK_DEPRECATIONS: tuple[PluginSdkDeprecation, ...] = ()

__all__ = [
    "PLUGIN_DIGEST_SCHEMA_VERSION",
    "PLUGIN_IDENTITY_SCOPE_VERSION",
    "PLUGIN_SDK_COMPATIBILITY",
    "PLUGIN_SDK_VERSION",
    "SDK_DEPRECATIONS",
    "SDK_DEPRECATION_WINDOW_MINOR_RELEASES",
    "UNIT_ANGSTROM",
    "UNIT_HARTREE",
    "ErrorKind",
    "MethodSpec",
    "PluginCapabilityHandler",
    "PluginFailureReport",
    "PluginIdentityScope",
    "PluginOutputSpec",
    "PluginRollbackAction",
    "PluginRollbackGuidance",
    "PluginSdkDeprecation",
    "PluginVersionDigest",
    "Provenance",
    "ResolvedDependency",
    "StructureInput",
    "TaskCancelledError",
    "TaskContext",
    "TaskInputError",
    "TaskKind",
    "TaskRequest",
    "TaskResources",
    "TaskResult",
    "check_cancelled",
    "check_deadline",
    "failure_report",
    "invoke_capability",
    "lock_plugin",
    "require_compatible_plugin",
    "resolve_context",
    "resolve_dependency_versions",
    "validate_result_contract",
]
