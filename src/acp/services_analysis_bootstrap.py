"""`acp`-side composition root for the application analysis registry (M2-02).

The application's explicit-analysis surface (``POST /api/v1/jobs/{id}/analyze``)
is registry-gated by :mod:`acp_services.runtime.analysis_capabilities`.  B06
shipped that registry as an empty shell with a process-global default; this
module is the composition root that builds the **application-level** registry and
registers a real handler for ``vibration.analyze``:

* ``vibration.analyze`` — **executable**.  The handler consumes only *prepared*
  artifacts of the route's task (each ``ArtifactRef`` is resolved through the
  persistent prepare-operation authority and its published body is re-verified
  against the committed digest), parses the body with CCCP's pure viewer parse
  and delegates exactly once to A's declared pure-analysis entry point
  (``cccp.analysis.frequency_products.build_frequency_report``) through
  :func:`cccp.calculation.capabilities.bind_capability`.  It never starts a task,
  a subprocess, a QC program or a plugin.
* ``vibration.compute`` — **declared, not executable** here.  It is a compute
  capability that runs exactly one ``frequency`` task; submitting it as a task is
  the only path, so an analyze request for it is refused with
  ``analysis_capability_not_executable``.  This is what keeps the frozen
  ``registered != executable`` distinction visible in production.

The registry is injected onto ``app.state.analysis_registry`` by
:func:`acp.api.server.create_app` at lifespan startup; nothing mutates the
module-level ``DEFAULT_ANALYSIS_REGISTRY``.

No QC/executable/LSF/model call is reachable from this module.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from pathlib import Path
from typing import Any

from acp_services.data.artifacts import (
    MAX_ARTIFACT_BYTES,
    ArtifactRead,
    ArtifactStore,
    resolve_artifact_read,
)
from acp_services.data.operations import PrepareOperationService
from acp_services.runtime.analysis_capabilities import (
    AnalysisCapability,
    AnalysisCapabilityRegistry,
)
from cccp.calculation.capabilities import bind_capability
from cccp.calculation.products.contracts import compute_input_digest

logger = logging.getLogger(__name__)

__all__ = [
    "ANALYSIS_PRODUCT_SCHEMA",
    "VIBRATION_ANALYZE_ID",
    "VIBRATION_ANALYZE_VERSION",
    "VibrationAnalyzeHandler",
    "build_analysis_registry",
]

#: The pure-analysis capability this application can execute.
VIBRATION_ANALYZE_ID = "vibration.analyze"
#: The compute capability declared (never executed) on this surface.
VIBRATION_COMPUTE_ID = "vibration.compute"
#: Version of the built-in ``vibration.analyze`` registration.
VIBRATION_ANALYZE_VERSION = "1.0.0"
#: Wire schema id of the produced analysis product document.
ANALYSIS_PRODUCT_SCHEMA = "acp2.science.vibration_analysis.v1"
#: Owner of the science semantics registered here.
_SCIENCE_OWNER = "A"
#: Owner of the artifact/read lifecycle backing the handler.
_PLATFORM_OWNER = "B12"

#: The only analysis input format the built-in handler reads.  An ORCA output is
#: the format CCCP's frequency science parses; anything else is a typed refusal
#: rather than a guess.
_SUPPORTED_INPUT_FORMATS: tuple[str, ...] = ("orca-output",)
#: Declared parameter keys of the built-in handler.
_SUPPORTED_PARAMETER_KEYS: frozenset[str] = frozenset({"format"})


class VibrationAnalysisInputError(ValueError):
    """The prepared artifact cannot be read as a vibration analysis input."""

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


@dataclass
class VibrationAnalyzeHandler:
    """Pure analysis over already-prepared artifacts (no task, no QC).

    Args:
        prepare_operations: The application's persistent prepare-operation
            authority (the same instance the prepare route/worker use).
        artifact_store: The controlled artifact publication store.
        permission_scope: Permission scope of the prepare operations.
        max_input_bytes: Server-side read bound for one analysis input (the
            artifact publication cap; internal, never a client read bound).
    """

    prepare_operations: PrepareOperationService
    artifact_store: ArtifactStore
    permission_scope: str = "local-single"
    max_input_bytes: int = MAX_ARTIFACT_BYTES
    delegation_target: str = dataclass_field(default_factory=lambda: _delegation_target())

    def __call__(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Run ``vibration.analyze`` over the request's prepared artifacts."""
        parameters = request.get("parameters")
        params = dict(parameters) if isinstance(parameters, Mapping) else {}
        input_format = str(params.get("format") or _SUPPORTED_INPUT_FORMATS[0])
        if input_format not in _SUPPORTED_INPUT_FORMATS:
            raise VibrationAnalysisInputError(
                f"unsupported vibration analysis input format {input_format!r}",
                params={
                    "field": "parameters.format",
                    "rule": "unsupported_format",
                    "allowed": list(_SUPPORTED_INPUT_FORMATS),
                },
            )
        undeclared = sorted(name for name in params if name not in _SUPPORTED_PARAMETER_KEYS)
        if undeclared:
            raise VibrationAnalysisInputError(
                f"vibration.analyze does not accept the parameter {undeclared[0]!r}",
                params={
                    "field": f"parameters.{undeclared[0]}",
                    "rule": "unknown_field",
                    "fields": undeclared,
                },
            )

        references = _coerce_refs(request.get("input_refs"))
        reads = [self._read_verified(ref) for ref in references]
        # The first prepared artifact is the science input; every reference is
        # still verified, so an unrelated/unprepared ref can never ride along.
        primary = reads[0]
        try:
            text = primary.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VibrationAnalysisInputError(
                "the prepared artifact is not a UTF-8 text frequency output",
                params={
                    "field": "input_refs[0]",
                    "rule": "unsupported_format",
                    "reason": "not_text",
                },
            ) from exc
        report = self._analyse_text(text)
        product_digest = compute_input_digest(report)
        inputs = [read.ref.to_dict() for read in reads]
        return {
            "schema_version": 1,
            "analysis_id": VIBRATION_ANALYZE_ID,
            "state": "computed",
            "started": False,
            "inputs": inputs,
            "product": {
                "schema_version": ANALYSIS_PRODUCT_SCHEMA,
                "digest": product_digest,
                "input_digests": [read.ref.digest for read in reads],
                "format": input_format,
                "report": report,
            },
            "provenance": {
                "capability_id": VIBRATION_ANALYZE_ID,
                "capability_version": VIBRATION_ANALYZE_VERSION,
                "delegation_target": self.delegation_target,
                "input_digests": [read.ref.digest for read in reads],
                "starts_subprocess": False,
                "starts_task": False,
            },
        }

    # -- artifact read (verified, cache-only) ------------------------------

    def _read_verified(self, ref: Mapping[str, Any]) -> ArtifactRead:
        """Resolve one prepared artifact; a refusal is a named ArtifactError."""
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

    # -- science (pure CCCP) ----------------------------------------------

    @staticmethod
    def _analyse_text(text: str) -> dict[str, Any]:
        """Delegate to A's declared pure-analysis entry point exactly once.

        The viewer parse is CCCP's single ORCA aggregate parse
        (:class:`cccp.analysis.orca_parser.OrcaOutputParser`); the science is
        A's declared ``vibration.analyze`` binding, which resolves
        ``cccp.analysis.frequency_products.build_frequency_report``.  No task
        module is imported and no subprocess is reachable.
        """
        from cccp.analysis.orca_parser import OrcaOutputParser

        calculation = OrcaOutputParser().parse_text(text)
        binding = bind_capability(VIBRATION_ANALYZE_ID, analysis_input=calculation)
        preflight = binding.preflight()
        if not preflight.ready:
            raise VibrationAnalysisInputError(
                "the prepared artifact does not carry usable frequency products",
                params={
                    "field": "input_refs[0]",
                    "rule": "invalid_scientific_input",
                    "checks": [check.to_dict() for check in preflight.checks],
                },
                http_status=409,
            )
        report = binding.execute()
        if not isinstance(report, Mapping):  # pragma: no cover - contract drift guard
            raise VibrationAnalysisInputError(
                "the analysis entry point did not return a report object",
                params={"rule": "invalid_scientific_input"},
                http_status=500,
            )
        return dict(report)


def _delegation_target() -> str:
    """Return the declared pure-analysis entry point (A's binding is the source)."""
    binding = bind_capability(VIBRATION_ANALYZE_ID)
    target = binding.delegation_target
    return str(target) if target else "cccp.analysis.frequency_products.build_frequency_report"


def _coerce_refs(value: Any) -> tuple[Mapping[str, Any], ...]:
    """Return the normalized ArtifactRef mappings of one analyze request.

    The request was already normalised by
    :func:`acp_services.entrypoints.application_params.normalize_analyze` (which
    enforces the full identity tuple).  A handler called directly (tests) gets
    the same refusal instead of a silently empty analysis.
    """
    if not isinstance(value, (list, tuple)) or not value:
        raise VibrationAnalysisInputError(
            "analysis requires at least one prepared artifact reference",
            params={"field": "input_refs", "rule": "required"},
        )
    refs: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise VibrationAnalysisInputError(
                "input_refs entries must be ArtifactRef objects",
                params={"field": f"input_refs[{index}]", "rule": "type"},
            )
        missing = [
            name
            for name in ("task_id", "attempt", "artifact_id", "generation", "digest")
            if item.get(name) in (None, "")
        ]
        if missing:
            raise VibrationAnalysisInputError(
                f"input_refs[{index}].{missing[0]} is required",
                params={
                    "field": f"input_refs[{index}].{missing[0]}",
                    "rule": "missing_artifact_ref",
                },
            )
        refs.append(dict(item))
    return tuple(refs)


def build_analysis_registry(
    *,
    prepare_operations: PrepareOperationService | None,
    artifact_store_root: str | Path,
    permission_scope: str = "local-single",
    max_input_bytes: int = MAX_ARTIFACT_BYTES,
) -> AnalysisCapabilityRegistry:
    """Build the application-level analysis registry (M2-02).

    A fresh :class:`AnalysisCapabilityRegistry` is returned on every call; the
    module-level ``DEFAULT_ANALYSIS_REGISTRY`` is never used as the production
    authority.  The registry is stored on ``app.state.analysis_registry`` by the
    application factory and is the only table the analyze command reads.

    Args:
        prepare_operations: The application's prepare-operation authority
            (``JobManager``-owned).  ``None`` means the process cannot serve
            prepared bodies, so the handler is not registered as executable.
        artifact_store_root: Root of the controlled artifact publication store.
        permission_scope: Scope of the prepare operations to read.
        max_input_bytes: Server-side input read bound (artifact publication cap).

    Returns:
        The registry with ``vibration.analyze`` executable when the artifact
        authority exists, and ``vibration.compute`` declared but not executable.
    """
    registry = AnalysisCapabilityRegistry()
    store = ArtifactStore(artifact_store_root)
    if prepare_operations is None:
        # No persistent prepare authority: declare the semantics, execute
        # nothing.  ``registered`` is an honest state — it is never upgraded to
        # ``executable`` without a real handler.
        registry.register(
            AnalysisCapability(
                analysis_id=VIBRATION_ANALYZE_ID,
                version=VIBRATION_ANALYZE_VERSION,
                owner=_SCIENCE_OWNER,
                handler=None,
                metadata={
                    "reason": "prepare_operations_unavailable",
                    "inputs": "prepared artifact_ref only",
                },
            )
        )
    else:
        handler = VibrationAnalyzeHandler(
            prepare_operations=prepare_operations,
            artifact_store=store,
            permission_scope=permission_scope,
            max_input_bytes=max_input_bytes,
        )
        registry.register(
            AnalysisCapability(
                analysis_id=VIBRATION_ANALYZE_ID,
                version=VIBRATION_ANALYZE_VERSION,
                owner=_SCIENCE_OWNER,
                handler=handler,
                metadata={
                    "execution_semantics": "pure_analysis",
                    "starts_subprocess": False,
                    "starts_task": False,
                    "inputs": "prepared artifact_ref only",
                    "delegation_target": _delegation_target(),
                    "input_formats": list(_SUPPORTED_INPUT_FORMATS),
                    "product_schema": ANALYSIS_PRODUCT_SCHEMA,
                },
            )
        )
    # ``vibration.compute`` is a *compute* capability: it runs exactly one
    # ``frequency`` task through the normal submit path.  Registering it here
    # without a handler keeps the frozen registered/executable distinction real:
    # an analyze request for it is refused, never silently executed.
    registry.register(
        AnalysisCapability(
            analysis_id=VIBRATION_COMPUTE_ID,
            version=VIBRATION_ANALYZE_VERSION,
            owner=_PLATFORM_OWNER,
            handler=None,
            metadata={
                "execution_semantics": "compute",
                "delegates_to_task_kind": "frequency",
                "starts_subprocess": True,
                "starts_task": True,
                "analysis_surface": "not_a_read_only_analysis",
                "submit_as": "workflow:frequency",
            },
        )
    )
    logger.debug(
        "analysis registry built: %s", [entry["analysis_id"] for entry in registry.describe()]
    )
    return registry
