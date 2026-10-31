"""Composition root for installed third-party science plugin analysis (M2-05).

This is the `acp`-side half of the production plugin assembly.  It owns exactly
two things and refuses to own a third:

1. **Wiring** — build the
   :class:`~acp_services.runtime.science_plugins.SciencePluginRegistry` for a run
   root (its env-driven trust allow-list lives here) and turn every enabled,
   byte-verified **pure-analysis** capability into an
   :class:`~acp_services.runtime.analysis_capabilities.AnalysisCapability` with a
   real handler.  The handler is registered into the **injected** application
   registry, exactly like the built-in ``vibration.analyze`` registration of
   M2-02 — never into the module-level default.
2. **Execution** — :class:`PluginAnalysisHandler` consumes only *prepared*
   artifacts of the route's task.  Each ``ArtifactRef`` is re-verified through
   the persistent prepare-operation authority and the committed artifact digest
   before the plugin sees a byte; the verified body is materialised into a
   controlled scratch file (never a caller path), the request envelope is built
   through the existing CCCP task binding, and the capability is invoked through
   :func:`cccp.plugin.sdk.invoke_capability` (the single plugin execution
   contract, disabled-by-default host, no second execution station).

No subprocess, no QC program, no LSF, no task submission and no model call is
reachable from this module.  The plugin cannot start a process either: the
manifest declares ``requires_subprocess: false`` and the SDK refuses a
pure-analysis capability that declares otherwise.  The plugin module itself is
imported only when an *enabled* capability is invoked.

Environment (all optional; absent means no plugin is trusted/enabled):

* ``ACP_SCIENCE_PLUGIN_TRUSTED_CAPABILITIES`` — comma-separated capability ids.
* ``ACP_SCIENCE_PLUGIN_TRUSTED_PLUGINS`` — comma-separated plugin names.
* ``ACP_SCIENCE_PLUGIN_AUTHORIZED_PRINCIPALS`` — comma-separated issuers
  (defaults to ``controller``).
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acp_services.data.artifacts import (
    MAX_ARTIFACT_BYTES,
    ArtifactRead,
    ArtifactStore,
    resolve_artifact_read,
)
from acp_services.data.operations import PrepareOperationService
from acp_services.runtime.analysis_capabilities import AnalysisCapability
from acp_services.runtime.science_plugins import (
    PLUGIN_DESCRIPTOR_SCHEMA,
    PluginEnablement,
    SciencePluginRegistry,
)
from cccp.calculation.capabilities.task_binding import build_task_request
from cccp.calculation.products.contracts import compute_input_digest
from cccp.plugin import PluginHost, invoke_capability
from cccp.plugin.sdk import lock_plugin

logger = logging.getLogger(__name__)

__all__ = [
    "SCIENCE_PLUGIN_STATE_DIR",
    "PluginAnalysisHandler",
    "PluginAnalysisInputError",
    "build_plugin_analysis_capabilities",
    "build_science_plugin_registry",
]

#: Directory (under the run root) holding the enablement ledger and snapshots.
SCIENCE_PLUGIN_STATE_DIR = "science-plugins"

#: Trust allow-list of capability ids (comma separated).
TRUSTED_CAPABILITIES_ENV = "ACP_SCIENCE_PLUGIN_TRUSTED_CAPABILITIES"
#: Trust allow-list of plugin names (comma separated).
TRUSTED_PLUGINS_ENV = "ACP_SCIENCE_PLUGIN_TRUSTED_PLUGINS"
#: Principals allowed to issue an enable/disable authorization fact.
AUTHORIZED_PRINCIPALS_ENV = "ACP_SCIENCE_PLUGIN_AUTHORIZED_PRINCIPALS"

#: Declared analysis product schema for third-party results.
PLUGIN_ANALYSIS_PRODUCT_SCHEMA = "acp2.science.plugin_analysis.v1"

#: Envelope fields the platform never takes from the caller: the verified
#: prepared artifact is the only structure input a plugin analysis may read.
_RESERVED_ENVELOPE_FIELDS: frozenset[str] = frozenset({"structure", "output_dir"})


class PluginAnalysisInputError(ValueError):
    """The prepared artifact/envelope cannot be analysed by this capability.

    Carries the same machine-readable ``params``/``http_status`` shape as the
    built-in analysis input error so the analyze route maps both identically.
    """

    def __init__(
        self,
        message: str,
        *,
        params: Mapping[str, Any] | None = None,
        http_status: int = 400,
    ) -> None:
        super().__init__(message)
        self.params: dict[str, Any] = dict(params or {})
        self.http_status = int(http_status)


# ---------------------------------------------------------------------------
# Registry composition
# ---------------------------------------------------------------------------


def _split_allowlist(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def build_science_plugin_registry(
    *,
    run_root: str | Path,
    environ: Mapping[str, str] | None = None,
    entry_points: object = None,
    manifest_reader: Any | None = None,
) -> SciencePluginRegistry:
    """Build the installed-plugin enablement ledger for *run_root*.

    Explicit arguments win over the environment.  This function performs no
    discovery itself (``scan`` stays an explicit call) and imports no plugin.
    """
    source = environ if environ is not None else os.environ
    return SciencePluginRegistry(
        state_root=Path(run_root) / SCIENCE_PLUGIN_STATE_DIR,
        trusted_capabilities=_split_allowlist(source.get(TRUSTED_CAPABILITIES_ENV)),
        trusted_plugins=_split_allowlist(source.get(TRUSTED_PLUGINS_ENV)),
        authorized_principals=(
            _split_allowlist(source.get(AUTHORIZED_PRINCIPALS_ENV)) or ("controller",)
        ),
        entry_points=entry_points,
        manifest_reader=manifest_reader,
    )


# ---------------------------------------------------------------------------
# The pure-analysis handler
# ---------------------------------------------------------------------------


@dataclass
class PluginAnalysisHandler:
    """Run one enabled third-party pure-analysis capability over prepared bytes.

    Args:
        enablement: The frozen enablement record (identity + provenance).
        registry: The enablement ledger, re-read on every call so a runtime
            disable or an installed-bytes change is refused instead of ignored.
        host: The disabled-by-default plugin host with this plugin explicitly
            enabled (the operator fact is the ledger record).
        prepare_operations: The application's persistent prepare authority.
        artifact_store: The controlled artifact publication store.
        permission_scope: Scope of the prepare operations to read.
        max_input_bytes: Server-side input read bound (artifact publication cap).
    """

    enablement: PluginEnablement
    registry: SciencePluginRegistry
    host: PluginHost
    prepare_operations: PrepareOperationService
    artifact_store: ArtifactStore
    permission_scope: str = "local-single"
    max_input_bytes: int = MAX_ARTIFACT_BYTES
    scratch_root: Path | None = field(default=None, compare=False)

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Analyse the request's prepared artifacts with the plugin capability."""
        self._require_current_enablement()
        parameters = request.get("parameters")
        params = dict(parameters) if isinstance(parameters, Mapping) else {}
        undeclared = sorted(name for name in params if name != "envelope")
        if undeclared:
            raise PluginAnalysisInputError(
                f"{self.capability_id} does not accept the parameter {undeclared[0]!r}",
                params={
                    "field": f"parameters.{undeclared[0]}",
                    "rule": "unknown_field",
                    "fields": undeclared,
                },
            )
        envelope = params.get("envelope")
        if not isinstance(envelope, Mapping):
            raise PluginAnalysisInputError(
                "a third-party analysis request must declare the task envelope it consumes",
                params={"field": "parameters.envelope", "rule": "required"},
            )
        reserved = sorted(set(envelope) & _RESERVED_ENVELOPE_FIELDS)
        if reserved:
            raise PluginAnalysisInputError(
                f"parameters.envelope.{reserved[0]} is supplied by the platform",
                params={
                    "field": f"parameters.envelope.{reserved[0]}",
                    "rule": "platform_owned_field",
                },
            )
        task_kind = envelope.get("task")
        if not isinstance(task_kind, str) or not task_kind.strip():
            raise PluginAnalysisInputError(
                "parameters.envelope.task is required (the envelope identity to consume)",
                params={"field": "parameters.envelope.task", "rule": "required"},
            )

        references = _coerce_refs(request.get("input_refs"))
        reads = [self._read_verified(ref) for ref in references]
        form_digest = compute_input_digest(
            {
                "schema": "acp2.science.plugin-analysis-form.v1",
                "capability_id": self.capability_id,
                "capability_version": self.version,
                "parameters": params,
                "input_refs": [read.ref.to_dict() for read in reads],
            }
        )
        primary = reads[0]
        task_request, scratch_dir = self._build_request(task_kind, envelope, primary.body)
        try:
            result = invoke_capability(
                self.host,
                self.enablement.plugin_name,
                capability=self.capability_id,
                request=task_request,
            )
        finally:
            if scratch_dir is not None:
                shutil.rmtree(scratch_dir, ignore_errors=True)

        task_result = result.to_dict()
        product_digest = compute_input_digest(task_result)
        descriptor = self.enablement.descriptor
        output_schema = descriptor.get("output_schema")
        schema_ref = (
            str(output_schema.get("ref"))
            if isinstance(output_schema, Mapping) and output_schema.get("ref")
            else str(descriptor.get("output_schema") or "")
        )
        schema_version = (
            int(output_schema.get("schema_version") or 1)
            if isinstance(output_schema, Mapping)
            else 1
        )
        return {
            "schema_version": 1,
            "analysis_id": self.capability_id,
            "state": "computed",
            "started": False,
            "inputs": [read.ref.to_dict() for read in reads],
            "product": {
                "schema": PLUGIN_ANALYSIS_PRODUCT_SCHEMA,
                "schema_version": 1,
                "capability_output_schema": schema_ref,
                "capability_output_schema_version": schema_version,
                "digest": product_digest,
                "input_digests": [read.ref.digest for read in reads],
                "task_result": task_result,
            },
            "provenance": {
                "capability_id": self.capability_id,
                "capability_version": self.version,
                "capability_ref": f"{self.capability_id}@{self.version}",
                "plugin_name": self.enablement.plugin_name,
                "descriptor_schema": PLUGIN_DESCRIPTOR_SCHEMA,
                "descriptor_digest": self.enablement.descriptor_digest,
                "form_digest": form_digest,
                "computed_distribution_digest": self.enablement.computed_distribution_digest,
                "distribution_digest": dict(self.enablement.distribution_digest),
                "enabled_by": self.enablement.enabled_by,
                "enabled_at": self.enablement.enabled_at,
                "input_digests": [read.ref.digest for read in reads],
                "starts_subprocess": False,
                "starts_task": False,
                "executed_in": "controller",
            },
        }

    # -- identity --------------------------------------------------------

    @property
    def capability_id(self) -> str:
        return self.enablement.capability_id

    @property
    def version(self) -> str:
        return self.enablement.version

    def _require_current_enablement(self) -> None:
        """Refuse when the frozen fact no longer matches the installed/enabled state."""
        current = self.registry.enablement(self.capability_id, self.version)
        if current is None or not current.enabled:
            raise PluginAnalysisInputError(
                f"{self.capability_id}@{self.version} is no longer enabled",
                params={"rule": "capability_not_enabled"},
                http_status=409,
            )
        if current.descriptor_digest != self.enablement.descriptor_digest:
            raise PluginAnalysisInputError(
                f"{self.capability_id}@{self.version} descriptor changed since enablement",
                params={"rule": "capability_descriptor_changed"},
                http_status=409,
            )
        installed = [
            record
            for record in self.registry.installed_capabilities()
            if record.capability_id == self.capability_id and record.version == self.version
        ]
        if not installed:
            raise PluginAnalysisInputError(
                f"{self.capability_id}@{self.version} is no longer installed",
                params={"rule": "plugin_not_installed"},
                http_status=409,
            )
        if installed[0].distribution_digest.digest != self.enablement.computed_distribution_digest:
            raise PluginAnalysisInputError(
                f"the installed bytes of {self.enablement.plugin_name} changed since enablement",
                params={
                    "rule": "distribution_digest_mismatch",
                    "expected": self.enablement.computed_distribution_digest,
                    "computed": installed[0].distribution_digest.digest,
                },
                http_status=409,
            )

    # -- artifact read (verified, cache-only) ------------------------------

    def _read_verified(self, ref: Mapping[str, Any]) -> ArtifactRead:
        return resolve_artifact_read(
            self.prepare_operations,
            self.artifact_store,
            job_id=str(ref["task_id"]),
            attempt=int(ref["attempt"]),
            resource_id=str(ref["artifact_id"]),
            generation=int(ref["generation"]),
            digest=str(ref["digest"]),
            permission_scope=self.permission_scope,
            max_bytes=self.max_input_bytes,
        )

    # -- request envelope --------------------------------------------------

    def _build_request(
        self,
        task_kind: str,
        envelope: Mapping[str, Any],
        body: bytes,
    ) -> tuple[Any, Path | None]:
        """Build the built-in request envelope around the verified artifact body.

        The envelope comes from the existing CCCP task binding
        (:func:`cccp.calculation.capabilities.task_binding.build_task_request`),
        so schema version, option types and payload rules stay the task layer's
        single contract.  Only ``structure.path`` is supplied by the platform:
        it points at a controlled copy of the *prepared* bytes.
        """
        payload = {str(key): value for key, value in envelope.items() if key != "task"}
        scratch_dir = self._scratch_dir()
        target = scratch_dir / "prepared.input"
        target.write_bytes(body)
        payload["structure"] = {"path": str(target)}
        try:
            return build_task_request(task_kind, payload), scratch_dir
        except (ValueError, TypeError) as exc:
            shutil.rmtree(scratch_dir, ignore_errors=True)
            raise PluginAnalysisInputError(
                f"parameters.envelope is not a valid {task_kind} request: {exc}",
                params={"field": "parameters.envelope", "rule": "invalid_scientific_input"},
            ) from exc

    def _scratch_dir(self) -> Path:
        root = self.scratch_root or (self.artifact_store.root / ".analysis-scratch")
        root.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="m205-", dir=root))


def _coerce_refs(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return the normalized ArtifactRef mappings of one analyze request."""
    if not isinstance(value, (list, tuple)) or not value:
        raise PluginAnalysisInputError(
            "analysis requires at least one prepared artifact reference",
            params={"field": "input_refs", "rule": "required"},
        )
    refs: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise PluginAnalysisInputError(
                "input_refs entries must be ArtifactRef objects",
                params={"field": f"input_refs[{index}]", "rule": "type"},
            )
        missing = [
            name
            for name in ("task_id", "attempt", "artifact_id", "generation", "digest")
            if item.get(name) in (None, "")
        ]
        if missing:
            raise PluginAnalysisInputError(
                f"input_refs[{index}].{missing[0]} is required",
                params={
                    "field": f"input_refs[{index}].{missing[0]}",
                    "rule": "missing_artifact_ref",
                },
            )
        refs.append(dict(item))
    return tuple(refs)


# ---------------------------------------------------------------------------
# Registration into the injected analysis registry
# ---------------------------------------------------------------------------


def build_plugin_analysis_capabilities(
    *,
    registry: SciencePluginRegistry,
    prepare_operations: PrepareOperationService | None,
    artifact_store_root: str | Path,
    permission_scope: str = "local-single",
    max_input_bytes: int = MAX_ARTIFACT_BYTES,
    entry_points: object = None,
    manifest_reader: Any | None = None,
) -> tuple[AnalysisCapability, ...]:
    """Project every enabled pure-analysis capability into a registry entry.

    A capability is registered **executable** only when the ledger says enabled,
    the distribution is installed, and the installed bytes still hash to the
    frozen distribution digest.  Without a prepare authority the entry is
    registered without a handler (``registered``), never fabricated as
    executable.
    """
    authorized = registry.authorized_capabilities()
    if not authorized:
        return ()
    store = ArtifactStore(artifact_store_root)
    host = PluginHost.from_science_entry_points(
        entry_points=entry_points,
        manifest_reader=manifest_reader,
    )
    capabilities: list[AnalysisCapability] = []
    for enablement in authorized:
        host.enable(enablement.plugin_name)
        metadata = host.catalog.get(enablement.plugin_name)
        lock_digest: str | None = None
        if metadata is not None:
            lock_digest = lock_plugin(metadata).digest
        handler = (
            PluginAnalysisHandler(
                enablement=enablement,
                registry=registry,
                host=host,
                prepare_operations=prepare_operations,
                artifact_store=store,
                permission_scope=permission_scope,
                max_input_bytes=max_input_bytes,
            )
            if prepare_operations is not None
            else None
        )
        capabilities.append(
            AnalysisCapability(
                analysis_id=enablement.capability_id,
                version=enablement.version,
                owner=f"plugin:{enablement.plugin_name}",
                handler=handler,
                metadata={
                    "execution_semantics": "pure_analysis",
                    "source": "science_plugin",
                    "plugin_name": enablement.plugin_name,
                    "starts_subprocess": False,
                    "starts_task": False,
                    "inputs": "prepared artifact_ref only",
                    "descriptor_digest": enablement.descriptor_digest,
                    "computed_distribution_digest": enablement.computed_distribution_digest,
                    "plugin_lock_digest": lock_digest,
                    "enabled_by": enablement.enabled_by,
                    "enabled_at": enablement.enabled_at,
                },
            )
        )
    logger.info(
        "registered third-party analysis capabilities: %s",
        [entry.analysis_id for entry in capabilities],
    )
    return tuple(capabilities)


def describe_enablements(enablements: Sequence[PluginEnablement]) -> list[dict[str, Any]]:
    """Return the CLI-safe JSON projection of the ledger records."""
    return [
        {
            "capability_id": record.capability_id,
            "plugin_name": record.plugin_name,
            "version": record.version,
            "installed": record.installed,
            "installed_at": record.installed_at,
            "enabled": record.enabled,
            "enabled_by": record.enabled_by,
            "enabled_at": record.enabled_at,
            "descriptor_digest": record.descriptor_digest,
            "computed_distribution_digest": record.computed_distribution_digest,
        }
        for record in enablements
    ]
