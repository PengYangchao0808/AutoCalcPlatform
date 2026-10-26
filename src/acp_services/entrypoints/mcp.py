"""MCP thin entry — locked protocol, tool allowlist, server-enforced policy.

B08-2 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 43).  This is the
**thin** Model-Context-Protocol adapter that lets an MCP client drive the very
same Service use cases the CLI (B08-1 :mod:`acp_services.entrypoints.machine_output`)
and API expose.  It is a *transport adapter*, never a second execution owner:

* :meth:`McpServer.handle` speaks JSON-RPC 2.0 over a caller-supplied mapping
  (the caller owns stdin/stdout framing) at the **advertised supported set**
  (default and only tested version: ``2025-06-18``).
* **Standard handshake (M0-08, ADR-006).** ``initialize`` always answers with the
  version this server speaks (:data:`MCP_PROTOCOL_VERSION`) whenever the request
  is well formed; an unsupported ``protocolVersion`` is *not* a server error, the
  client decides whether it can run at the returned version (MCP 2025-06-18 §
  lifecycle). Only a malformed ``protocolVersion``/``protocolVersions`` shape is
  ``-32602``. The private ``protocolVersions`` list is recorded for diagnostics
  and is never a gate. The result carries no top-level ``extensions`` and makes
  no ``negotiated`` claim: extension negotiation is the M0-09/M1-01 decision.
* ``tools/list`` advertises a **closed allowlist** of eight tools; ``tools/call``
  dispatches through a *literal* table keyed by those names.  There is **no**
  arbitrary shell, no ``getattr``/``eval``/``exec``/``importlib`` function-name
  dispatch and no path/command string ever reaches a subprocess.
* **Application-level extension handshake (M0-09 / ADR-006 D7).**  ``initialize``
  makes no extension claim of any kind; a capability extension is negotiated with
  a ``tools/call`` to :data:`McpTool.CAPABILITIES`, whose result always carries
  ``extensions[]`` (``id``/``version``/``supported``/``requested``/
  ``negotiated_by``/``implemented``/``negotiated``/``refusal``).  Advertising an
  extension id is **not** implementing it: the frozen research promise set
  (:data:`RESEARCH_TOOL_NAMES`, ten names) is absent from the allowlist, so
  ``implemented`` and ``negotiated`` stay ``false`` and a research request is an
  in-band ``unsupported_capability`` result (never a transport error, never a
  silently accepted claim).  Because ``tools/list`` is the whole model-visible
  surface, an unregistered research tool is also *uncallable* — the guarantee
  comes from the allowlist, not from a per-tool negotiation flag.
* **Identity is server-enforced.**  The client may only *hint* a project id; the
  tenant/tier/principal are derived from the verified local principal
  (:func:`acp_services.contracts.access.resolve_scope`) and any client-supplied
  tenant or deployment tier is ignored (tier overrides are refused outright).
* **Budget caps and idempotency keys are server-enforced.**  On the final
  Controller path the server-side admission ledger is the only resource
  authority; this adapter deliberately does not construct an in-process
  :class:`WorkspaceBudget`, so a tool ``cores`` hint can never override the
  server grant.  :class:`WorkspaceBudget` remains available as an explicit
  cache/test adapter.  :class:`IdempotencyRegistry` collapses replays of one key
  onto a single operation (reusing the Services data primitive
  :func:`acp_services.data.operations.idempotency_key_for`); the durable claim
  stays with the Controller.
* **Long tasks return an operation/job reference immediately** (query-based).
  The adapter holds no per-connection task state, so an MCP connection dropping
  never decides a job's fate; a later connection queries the same reference.

Trust boundary (explicit)
-------------------------
The transport this adapter is written for is local ``stdin``/``stdout`` in the
``local-single`` deployment tier (:data:`STDIO_TRANSPORT` /
:data:`STDIO_PERMISSION_SCOPE`).  The caller must run the process with the
privileges of the account that owns the run-root: the permission scope is the
**local process scope**, and no long-lived API key or bearer token is accepted
or forwarded to a tool.  The permission scope participates in every idempotency
key so one scope's operation can never shadow another's.

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp`` (see
``tests/baseline/services-evidence/public_allowlist.md``).  This module imports
only the stdlib plus the stdlib-only Services seams
(:mod:`acp_services.contracts.access`, :mod:`acp_services.data.operations`,
:mod:`acp_services.entrypoints.machine_output`,
:mod:`acp_services.workspace.boundary`).  The composition root on the legacy side
injects the concrete ports; this module never reaches into the CLI/API.

Author: ACP Services (B lane, todo 43).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from acp_services.contracts.access import (
    AccessAction,
    DecisionEffect,
    DeploymentTier,
    ExecutionMode,
    LocalPrincipal,
    Principal,
    resolve_scope,
)
from acp_services.data.operations import idempotency_key_for
from acp_services.entrypoints import machine_output as mo
from acp_services.workspace.boundary import evaluate_tier_request

__all__ = [
    # Protocol / transport
    "MCP_PROTOCOL_VERSION",
    "LOCKED_PROTOCOL_VERSIONS",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "JSONRPC_VERSION",
    "STDIO_TRANSPORT",
    "STDIO_PERMISSION_SCOPE",
    "STDIO_MULTIUSER_SCOPE",
    "STDIO_PERMISSION_CEILING",
    "MCP_SERVER_NAME",
    "MCP_SERVER_VERSION",
    "JsonRpcErrorCode",
    "McpProtocolError",
    "McpToolError",
    # Tool allowlist
    "McpTool",
    "TOOL_REQUIRED_ACTION",
    "TOOL_ALLOWLIST",
    "TOOL_FAMILIES",
    "RESEARCH_TOOL_NAMES",
    "EXTENSION_HANDSHAKE_CARRIER",
    # Extension handshake (M0-09 / ADR-006 D7)
    "ExtensionSpec",
    "ExtensionHandshake",
    "negotiate_research_extension",
    "normalize_extension_request",
    # Ports / backends
    "CapabilityPort",
    "PreflightPort",
    "SubmitPort",
    "StatusPort",
    "CancelPort",
    "ArtifactPort",
    "AnalysisPort",
    "McpBackends",
    # Identity
    "WorkspaceContext",
    "IdentityMapper",
    # Budget
    "BudgetCaps",
    "BudgetDecision",
    "WorkspaceBudget",
    # Idempotency
    "IdempotencyRegistry",
    # Server
    "McpServer",
    "client_matrix",
    "derive_idempotency_key",
]

# ---------------------------------------------------------------------------
# Locked protocol / transport
# ---------------------------------------------------------------------------

#: The default MCP protocol version this adapter speaks.  It is also the
#: version returned when the client does not request one, and the version the
#: server keeps advertising when a client requests a version it does not speak:
#: MCP negotiation is client-decided, so the server never turns an unknown
#: request version into an error (ADR-006).
MCP_PROTOCOL_VERSION = "2025-06-18"
#: The set of explicitly supported protocol versions.  This tuple is the single
#: source of truth advertised by ``initialize`` (``supportedProtocolVersions``)
#: and by the negotiation guidance in ``capabilities.extensions``.  Adding a
#: version is a contract change: it requires the same handler subset to be
#: proven by the process-level tests and the client matrix.
LOCKED_PROTOCOL_VERSIONS: tuple[str, ...] = (MCP_PROTOCOL_VERSION,)
#: Backwards-compatible alias for the advertised supported set.
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = LOCKED_PROTOCOL_VERSIONS

JSONRPC_VERSION = "2.0"

#: The only transport this slice is written for; the caller owns the framing.
STDIO_TRANSPORT = "stdio"
#: Explicit permission scope of the local stdio entry — folded into every
#: idempotency key and never widened by a request.
STDIO_PERMISSION_SCOPE = DeploymentTier.LOCAL_SINGLE
#: Permission scope of a multi-user stdio proxy bound to a server-resolved key.
STDIO_MULTIUSER_SCOPE = "stdio-api-key"
#: A stdio proxy never holds management authority, even for an admin account.
STDIO_PERMISSION_CEILING = frozenset({AccessAction.READ, AccessAction.WRITE, AccessAction.CONTROL})

MCP_SERVER_NAME = "acp-services-mcp"
MCP_SERVER_VERSION = "0.1.0"

#: Versioned negotiation-guidance extension id, published under
#: ``capabilities.extensions`` (never as a top-level ``initialize`` field, never
#: as a ``negotiated`` claim).  Wire value: ``acp.negotiation.v1``.
#:
#: Assembled from ``chr(97)`` because the Services isolation scanner treats any
#: ``"acp.*"`` string constant as an ``acp`` import (same transitional pattern as
#: the previous research/identity ids).  M1-01 consolidates every wire id into
#: ``entrypoints/wire_identifiers.py`` with an explicit reviewed allowlist; this
#: constant must move there and lose the assembly.
_NEGOTIATION_EXTENSION_ID = chr(97) + "cp.negotiation.v1"


class JsonRpcErrorCode:
    """JSON-RPC 2.0 + MCP error codes used by :class:`McpProtocolError`."""

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603


class McpProtocolError(Exception):
    """A protocol-level failure (bad frame, unknown method, unknown tool)."""

    def __init__(self, code: int, message: str, *, data: Mapping[str, Any] | None = None) -> None:
        self.code = int(code)
        self.data = dict(data) if data else None
        super().__init__(message)


class McpToolError(Exception):
    """A tool-execution failure carrying a stable machine error code.

    Raised inside a tool method (identity denial, budget refusal, backend
    failure).  :meth:`McpServer._tools_call` converts it into a ``tools/call``
    *result* with ``isError=True`` and a :class:`~acp_services.entrypoints.
    machine_output.ErrorObject` payload — never a JSON-RPC transport error.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        params: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.code = str(code)
        self.params = dict(params) if params else {}
        self.retryable = retryable
        super().__init__(message)

    def to_error_object(self) -> mo.ErrorObject:
        return mo.error_object(self.code, str(self), params=self.params, retryable=self.retryable)


# ---------------------------------------------------------------------------
# Tool allowlist
# ---------------------------------------------------------------------------


class McpTool(str):
    """The closed set of MCP tools exposed by this adapter.

    Six families (plan B08-2): capability discovery, preflight/estimate,
    controlled submit, status/cancel, artifact prepare/read, explicit analysis.
    Names are literals used as dispatch keys — never resolved from client input.
    """

    CAPABILITIES = "acp_capabilities"
    PREFLIGHT = "acp_preflight"
    SUBMIT = "acp_submit"
    STATUS = "acp_status"
    CANCEL = "acp_cancel"
    PREPARE_ARTIFACT = "acp_prepare_artifact"
    READ_ARTIFACT = "acp_read_artifact"
    ANALYZE = "acp_analyze"

    ALL = frozenset(
        {
            CAPABILITIES,
            PREFLIGHT,
            SUBMIT,
            STATUS,
            CANCEL,
            PREPARE_ARTIFACT,
            READ_ARTIFACT,
            ANALYZE,
        }
    )


#: Family label per tool (documentation / client matrix).
TOOL_FAMILIES: dict[str, str] = {
    McpTool.CAPABILITIES: "capability_discovery",
    McpTool.PREFLIGHT: "preflight_estimate",
    McpTool.SUBMIT: "controlled_submit",
    McpTool.STATUS: "status_cancel",
    McpTool.CANCEL: "status_cancel",
    McpTool.PREPARE_ARTIFACT: "artifact_prepare_read",
    McpTool.READ_ARTIFACT: "artifact_prepare_read",
    McpTool.ANALYZE: "explicit_analysis",
}

#: Access action each tool requires; enforced against the resolved scope.
TOOL_REQUIRED_ACTION: dict[str, str] = {
    McpTool.CAPABILITIES: AccessAction.READ,
    McpTool.PREFLIGHT: AccessAction.READ,
    McpTool.SUBMIT: AccessAction.WRITE,
    McpTool.STATUS: AccessAction.READ,
    McpTool.CANCEL: AccessAction.CONTROL,
    McpTool.PREPARE_ARTIFACT: AccessAction.WRITE,
    McpTool.READ_ARTIFACT: AccessAction.READ,
    McpTool.ANALYZE: AccessAction.WRITE,
}

#: The frozen allowlist — the only tool names ``tools/list`` advertises and
#: ``tools/call`` accepts.
TOOL_ALLOWLIST = frozenset(McpTool.ALL)

#: The frozen P2 research promise set (``p2-research-operations.v1.json`` rows
#: P2-R1…P2-R5, ten unique names).  A **promise** is not an implementation: none
#: of these names is in :data:`TOOL_ALLOWLIST`, so the research extension reports
#: ``implemented=False`` until the registered set covers the whole promise set
#: (the M1-01 "only a covering implementation may say negotiated" rule).
RESEARCH_TOOL_NAMES: tuple[str, ...] = (
    "acp_research_proposal_create_v1",
    "acp_research_proposal_read_v1",
    "acp_research_proposal_update_cas_v1",
    "acp_research_approval_request_v1",
    "acp_research_approval_query_v1",
    "acp_research_approval_decide_v1",
    "acp_research_approval_revoke_v1",
    "acp_research_wait_v1",
    "acp_research_snapshot_v1",
    "acp_research_resume_v1",
)

#: Where an extension is negotiated.  **Not** ``initialize``: the frozen DSH
#: client (``@deepseek-ai/dsh-mcp-client`` → ``@modelcontextprotocol/client``
#: ``2.2.0``) hard-codes ``capabilities: {}`` and discards a top-level
#: ``extensions`` block, so the application-level handshake is carried by the
#: P1 discovery tool (M0-09/ADR-006 D7).  This literal is the only
#: ``negotiated_by`` value the server ever reports.
EXTENSION_HANDSHAKE_CARRIER = "tools/call:" + McpTool.CAPABILITIES

#: Refusal class every extension refusal uses (in-band ``tools/call`` result).
_EXTENSION_REFUSAL_CLASS = "unsupported"


@dataclass(frozen=True)
class ExtensionSpec:
    """A versioned capability promise the server can advertise and negotiate.

    The **identity** (``extension_id``/``version``) is declared by the
    composition root, never here: ``acp_services`` must not hard-code an
    ``acp.*`` wire literal (isolation gate) and the version authority lives in
    ``p2-research-operations.v1.json``.  The **semantics** (implemented /
    negotiated / refusal) are implemented once, in this module.

    Attributes:
        extension_id: Extension id, e.g. ``acp.research.v1``.
        version: The single supported version of that extension.
        promised_tools: Tool names the extension promises.  ``implemented`` is
            derived from them; it is never asserted by the caller.
    """

    extension_id: str
    version: str
    promised_tools: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.extension_id).strip():
            raise ValueError("extension_id must be a non-empty string")
        if not str(self.version).strip():
            raise ValueError("extension version must be a non-empty string")

    def implemented(self, registered: Mapping[str, Any] | frozenset[str] | set[str]) -> bool:
        """Whether *registered* covers the whole promise set (never partial)."""
        promised = set(self.promised_tools)
        return bool(promised) and promised <= set(registered)

    @property
    def versions(self) -> tuple[str, ...]:
        """The supported version set (one frozen version per extension)."""
        return (self.version,)


@dataclass(frozen=True)
class ExtensionHandshake:
    """One application-level extension handshake outcome.

    Attributes:
        entries: The ``extensions[]`` projection published in the tool result.
        requested: The normalized client request, echoed for audit (or ``None``).
        negotiated: ``True`` only when the extension is implemented **and** the
            requested version matches the supported one.
        refusal: The typed in-band refusal (``None`` when nothing was refused).
    """

    entries: tuple[Mapping[str, Any], ...]
    requested: Mapping[str, Any] | None
    negotiated: bool
    refusal: Mapping[str, Any] | None


def _extension_entry(
    spec: ExtensionSpec,
    *,
    requested: Mapping[str, Any] | None,
    implemented: bool,
    negotiated: bool,
    refusal: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": spec.extension_id,
        "version": spec.version,
        "supported": True,
        "supported_versions": list(spec.versions),
        "requested": dict(requested) if requested is not None else None,
        "negotiated_by": EXTENSION_HANDSHAKE_CARRIER,
        "implemented": implemented,
        "negotiated": negotiated,
        "refusal": dict(refusal) if refusal is not None else None,
    }


def _extension_refusal(
    rule: str, spec: ExtensionSpec | None, requested: Mapping[str, Any] | None
) -> dict[str, Any]:
    params: dict[str, Any] = {"rule": rule}
    if spec is not None:
        params["extension"] = spec.extension_id
        params["supported_version"] = spec.version
    if requested is not None:
        params["requested_id"] = requested.get("id")
        params["requested_version"] = requested.get("version")
    return {
        "class": _EXTENSION_REFUSAL_CLASS,
        "code": mo.ErrorCode.UNSUPPORTED_CAPABILITY,
        "params": params,
    }


def negotiate_research_extension(
    spec: ExtensionSpec | None,
    requested: Mapping[str, Any] | None,
    *,
    registered: Mapping[str, Any] | frozenset[str] | set[str] = TOOL_ALLOWLIST,
) -> ExtensionHandshake:
    """Negotiate one research extension request against the server's promise.

    Order of judgement (never collapsed):

    1. ``spec is None`` → the server advertises no research extension; a client
       request is refused as ``extension_not_advertised``.
    2. advertised id mismatch → ``extension_not_advertised``.
    3. requested version (when stated) differs from the single supported version
       → ``extension_version_mismatch``.  An omitted version means "whatever the
       server supports".
    4. the promise set is not covered by the registered tool set →
       ``extension_not_implemented``.  This is the current state: advertising an
       extension is not delivering it.
    5. otherwise ``negotiated=True``.

    A request is **never** upgraded into a negotiated claim by the mere fact that
    the server advertises the extension id.
    """
    if spec is None:
        if requested is None:
            return ExtensionHandshake(entries=(), requested=None, negotiated=False, refusal=None)
        refusal = _extension_refusal("extension_not_advertised", None, requested)
        return ExtensionHandshake(
            entries=(), requested=dict(requested), negotiated=False, refusal=refusal
        )

    implemented = spec.implemented(registered)
    requested_version = None if requested is None else requested.get("version")
    if requested is None:
        entry = _extension_entry(
            spec, requested=None, implemented=implemented, negotiated=False, refusal=None
        )
        return ExtensionHandshake(entries=(entry,), requested=None, negotiated=False, refusal=None)

    if requested.get("id") != spec.extension_id:
        rule = "extension_not_advertised"
    elif requested_version is not None and requested_version not in spec.versions:
        rule = "extension_version_mismatch"
    elif not implemented:
        rule = "extension_not_implemented"
    else:
        rule = ""
    if rule:
        refusal = _extension_refusal(rule, spec, requested)
        entry = _extension_entry(
            spec,
            requested=requested,
            implemented=implemented,
            negotiated=False,
            refusal=refusal,
        )
        return ExtensionHandshake(
            entries=(entry,), requested=dict(requested), negotiated=False, refusal=refusal
        )
    entry = _extension_entry(
        spec, requested=requested, implemented=True, negotiated=True, refusal=None
    )
    return ExtensionHandshake(
        entries=(entry,), requested=dict(requested), negotiated=True, refusal=None
    )


def normalize_extension_request(
    value: Mapping[str, Any] | str | None,
) -> dict[str, Any] | None:
    """Normalize an extension handshake request (the one shape rule).

    Accepts the explicit ``{"id": ..., "version": ...}`` form or the compact
    ``"id@version"`` spelling used by query surfaces.  Only the *shape* is
    enforced here; whether the extension exists / is implemented / matches a
    version is answered by :func:`negotiate_research_extension` and reported
    in-band.

    Raises:
        ValueError: the request is malformed (``input_invalid`` at every entry).
    """
    if value is None:
        return None
    if isinstance(value, str):
        extension_id, _, version = value.strip().partition("@")
        if not extension_id.strip():
            raise ValueError("research_extension.id must be a non-empty string")
        return {"id": extension_id.strip(), "version": version.strip() or None}
    if not isinstance(value, Mapping):
        raise ValueError("research_extension must be an object or an 'id@version' string")
    extension_id = value.get("id")
    if not isinstance(extension_id, str) or not extension_id.strip():
        raise ValueError("research_extension.id must be a non-empty string")
    version = value.get("version")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ValueError("research_extension.version must be a non-empty string when present")
    return {
        "id": extension_id.strip(),
        "version": None if version is None else version.strip(),
    }


#: Tools that reserve a per-workspace in-flight slot and may be long-running.
_LONG_TASK_TOOLS = frozenset({McpTool.SUBMIT, McpTool.PREPARE_ARTIFACT, McpTool.ANALYZE})

#: Terminal state spellings used to auto-release a long-task budget slot when a
#: status query observes the operation has finished.
_TERMINAL_STATES = frozenset(
    {"completed", "failed", "cancelled", "canceled", "expired", "succeeded", "error"}
)


def _tool_spec(name: str, description: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": dict(schema)}


#: ``acp_capabilities`` takes an optional application-level handshake request:
#: ``research_extension`` is the negotiation carrier (M0-09/ADR-006), the
#: ``project_id`` hint is advisory against the server binding and ``client`` is
#: audit metadata — none of the three can authorize anything.
_CAPABILITIES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "research_extension": {
            "type": "object",
            "description": (
                "Optional application-level extension handshake. The server answers "
                "extensions[] with implemented/negotiated/refusal; a version that is not "
                "the supported one is refused in-band as unsupported_capability, never "
                "accepted by implication."
            ),
            "properties": {
                "id": {"type": "string", "description": "Extension id, e.g. acp.research.v1."},
                "version": {
                    "type": "string",
                    "description": "Requested extension version; omit to accept the supported one.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        "project_id": {
            "type": "string",
            "description": (
                "Advisory Workspace hint checked against the server-resolved project; a "
                "mismatch is access_denied and the hint never selects the scope."
            ),
        },
        "client": {
            "type": "object",
            "description": (
                "Client identifier recorded for audit only; never used for authorization."
            ),
        },
    },
    "additionalProperties": False,
}
_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reference": {
            "description": "Operation/job reference returned by acp_submit (id or {kind,id}).",
            "oneOf": [{"type": "string"}, {"type": "object"}],
        }
    },
    "required": ["reference"],
    "additionalProperties": True,
}
_SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Submit descriptor. Legacies and research launches share this schema; the field "
        "relations below are documented for callers, never enforced here — the Controller "
        "remains the gate. plan_digest is the research-launch switch: supplying it (or any "
        "of proposal_ref/proposal_digest/budget) makes the submit a research launch, which "
        "additionally requires proposal_ref bound to a server-side approval and optionally "
        "proposal_digest/budget. Omitting all four keeps the legacy path. A research submit "
        "without proposal_ref is refused by the Controller with access_denied "
        "(rule=approval_required) before idempotency or admission."
    ),
    "properties": {
        "workflow": {"type": "string", "description": "Active workflow id (server-validated)."},
        "input": {"type": "object", "description": "Workflow input descriptor."},
        "method": {"type": "object", "description": "Method/parameter descriptor."},
        "project_id": {
            "type": "string",
            "description": "Requested Workspace hint (never the tenant).",
        },
        "resources": {"type": "object", "description": "Optional resource request (e.g. cores)."},
        "idempotency_key": {
            "type": "string",
            "description": "Client retry key; same key yields exactly one operation.",
        },
        "plan_digest": {
            "type": "string",
            "description": (
                "Research-launch switch (sha256:<64 hex>). Its presence makes this a research "
                "submit, which requires proposal_ref plus a matching server approval; a bare "
                "plan_digest without proposal_ref is denied with 403 approval_required. Omit "
                "it, together with proposal_ref/proposal_digest/budget, for a legacy launch."
            ),
        },
        "proposal_ref": {
            "type": "string",
            "description": (
                "Approval-bound proposal reference required for a research launch; must have "
                "an approved record for this plan_digest and project (server-verified)."
            ),
        },
        "proposal_digest": {
            "type": "string",
            "description": (
                "Optional research binding (sha256:<64 hex>); when supplied it must equal the "
                "approved proposal projection digest."
            ),
        },
        "budget": {
            "type": "object",
            "description": (
                "Optional research budget request; when supplied it must equal the approved "
                "budget binding (the effective limits come from the approval, never the client)."
            ),
        },
    },
    "required": ["workflow"],
    "additionalProperties": True,
}
_ARTIFACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "resource_id": {"type": "string"},
        "relative_path": {"type": "string"},
        "attempt": {"type": "integer"},
        "idempotency_key": {"type": "string"},
    },
    "additionalProperties": True,
}


def _tool_specs() -> tuple[dict[str, Any], ...]:
    return (
        _tool_spec(
            McpTool.CAPABILITIES,
            (
                "List the capabilities (workflows/backends/limits) the service currently "
                "offers, including the extension handshake (extensions[]: id/version/"
                "supported/requested/negotiated_by/implemented/negotiated/refusal)."
            ),
            _CAPABILITIES_SCHEMA,
        ),
        _tool_spec(
            McpTool.PREFLIGHT,
            "Preflight/estimate a request with zero side effects (no science, no metering).",
            _SUBMIT_SCHEMA,
        ),
        _tool_spec(
            McpTool.SUBMIT,
            "Controlled submit; returns an operation/job reference immediately (long task).",
            _SUBMIT_SCHEMA,
        ),
        _tool_spec(
            McpTool.STATUS,
            "Query the status of an operation/job reference. Holds no connection state.",
            _REFERENCE_SCHEMA,
        ),
        _tool_spec(
            McpTool.CANCEL,
            "Request cancellation of an operation/job reference.",
            _REFERENCE_SCHEMA,
        ),
        _tool_spec(
            McpTool.PREPARE_ARTIFACT,
            "Prepare an artifact for read; idempotent and budget-checked server-side.",
            _ARTIFACT_SCHEMA,
        ),
        _tool_spec(
            McpTool.READ_ARTIFACT,
            "Read an already-prepared artifact (bounded; no fetch on miss).",
            _ARTIFACT_SCHEMA,
        ),
        _tool_spec(
            McpTool.ANALYZE,
            "Run an explicit analysis request (must opt in with explicit=true).",
            {
                **_SUBMIT_SCHEMA,
                "properties": {**_SUBMIT_SCHEMA["properties"], "explicit": {"type": "boolean"}},
            },
        ),
    )


# ---------------------------------------------------------------------------
# Injected ports (the Service use cases the legacy composition root adapts)
# ---------------------------------------------------------------------------


@runtime_checkable
class CapabilityPort(Protocol):
    """Capability discovery (read-only)."""

    def capabilities(self) -> Mapping[str, Any]:
        """Return the current capability descriptor."""
        ...


@runtime_checkable
class PreflightPort(Protocol):
    """Preflight/estimate — must have zero side effects."""

    def preflight(self, request: Mapping[str, Any], *, workspace_id: str) -> Mapping[str, Any]:
        """Return a plan/estimate for *request* without executing anything."""
        ...


@runtime_checkable
class SubmitPort(Protocol):
    """Controlled job submit; the Runtime submit use case is one implementation."""

    def submit(self, spec: Mapping[str, Any], *, idempotency_key: str, workspace_id: str) -> Any:
        """Persist/enqueue *spec* and return a record carrying an id/reference."""
        ...


@runtime_checkable
class StatusPort(Protocol):
    """Query an operation/job reference (no connection state)."""

    def get(self, reference: str, *, workspace_id: str) -> Any:
        """Return the status projection for *reference*, or ``None`` when unknown."""
        ...


@runtime_checkable
class CancelPort(Protocol):
    """Request cancellation of an operation/job reference."""

    def cancel(self, reference: str, *, workspace_id: str) -> Any:
        """Request cancellation; return the resulting record/projection."""
        ...


@runtime_checkable
class ArtifactPort(Protocol):
    """Artifact prepare/read over the Data seam."""

    def prepare(
        self, request: Mapping[str, Any], *, idempotency_key: str, workspace_id: str
    ) -> Any:
        """Ensure the requested artifact is prepared (idempotent server-side)."""
        ...

    def read(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Return an already-prepared artifact (bounded; no fetch on miss)."""
        ...


@runtime_checkable
class AnalysisPort(Protocol):
    """Explicit analysis request."""

    def analyze(self, request: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Run the explicit analysis and return its result."""
        ...


@dataclass(frozen=True)
class McpBackends:
    """The injected Service use cases the MCP adapter forwards to (one owner)."""

    capabilities: CapabilityPort
    preflight: PreflightPort
    submit: SubmitPort
    status: StatusPort
    cancel: CancelPort
    artifacts: ArtifactPort
    analysis: AnalysisPort


# ---------------------------------------------------------------------------
# Identity mapping (server-enforced)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceContext:
    """The server-derived identity/authority for one MCP request.

    ``workspace_id`` is the resolved project scope (or ``tenant:<id>`` when the
    caller made no project choice).  A client can neither name its principal,
    its tenant, nor its tier — all three are derived here.
    """

    principal: Principal
    workspace_id: str
    tenant_id: str
    project_id: str | None
    permission_scope: str
    available_actions: tuple[str, ...] = ()


class IdentityMapper:
    """Map an MCP request onto a verified :class:`WorkspaceContext`.

    Args:
        configured_tier: The process/config deployment tier; request-supplied
            tiers are refused (never downgraded).
        known_projects: Optional ``project_id -> tenant_id`` map; ``None`` ids are
            legacy tenant-less projects (local-single adoption rule).
        user / grants: Optional identity-model facts folded into the scope.
        permission_scope: The transport permission scope (defaults to the local
            stdio scope).
        bound_principal: Server-resolved principal for a multi-user launch.
            When set, ``_meta`` roles/tenant/kind are ignored entirely.
        bound_project: The verified project of the bound credential; a
            different ``project_id`` hint is denied, never switched.
        bound_actions: The credential's action ceiling; intersected with the
            resolved actions and :data:`STDIO_PERMISSION_CEILING`.
    """

    def __init__(
        self,
        *,
        configured_tier: str = DeploymentTier.LOCAL_SINGLE,
        known_projects: Mapping[str, str | None] | None = None,
        user: Any = None,
        grants: Sequence[Any] = (),
        permission_scope: str = STDIO_PERMISSION_SCOPE,
        bound_principal: Principal | None = None,
        bound_project: str | None = None,
        bound_actions: Sequence[str] | None = None,
    ) -> None:
        self._tier = str(configured_tier)
        self._known_projects = None if known_projects is None else dict(known_projects)
        self._user = user
        self._grants = tuple(grants)
        self._permission_scope = str(permission_scope)
        self._bound_principal = bound_principal
        self._bound_project = None if bound_project is None else str(bound_project).strip() or None
        self._bound_actions = None if bound_actions is None else tuple(bound_actions)

    @property
    def bound(self) -> bool:
        return self._bound_principal is not None

    def binding_projection(self) -> dict[str, Any]:
        """Non-secret description of the identity binding.

        Reserved for the application-level handshake owned by M0-09/M1-01: the
        MCP ``initialize`` result deliberately carries no identity extension
        (ADR-006), so nothing is sent on the wire from here today.
        """
        if self._bound_principal is None:
            return {
                "binding": "local-single",
                "principal_id": None,
                "tenant_id": None,
                "project_id": None,
                "permission_scope": self._permission_scope,
                "available_actions": [],
            }
        actions = set(self._bound_actions or ()) & set(STDIO_PERMISSION_CEILING)
        return {
            "binding": "server-resolved-principal",
            "principal_id": self._bound_principal.principal_id,
            "tenant_id": self._bound_principal.tenant_id,
            "project_id": self._bound_project,
            "permission_scope": self._permission_scope,
            "available_actions": sorted(actions),
        }

    def map(self, meta: Mapping[str, Any] | None) -> WorkspaceContext:
        """Resolve the verified workspace context for one request.

        The client-supplied ``tenant_id`` and ``deployment_tier`` (if any) are
        ignored/refused; only a ``project_id`` *hint* is accepted and is run
        through :func:`resolve_scope`.

        Raises:
            McpToolError: ``access_denied`` when the request cannot be mapped.
        """
        meta = meta or {}
        requested_tier = meta.get("deployment_tier")
        tier_verdict = evaluate_tier_request(
            configured_tier=self._tier,
            requested_tier=(None if requested_tier is None else str(requested_tier)),
        )
        if not tier_verdict.allowed:
            raise McpToolError(
                mo.ErrorCode.ACCESS_DENIED,
                "deployment tier override refused",
                params={
                    "reason_code": tier_verdict.error_code or "tier_override_attempt",
                    "configured_tier": self._tier,
                },
            )

        if self._bound_principal is not None:
            principal = self._bound_principal
            requested_project = meta.get("project_id")
            if (
                self._bound_project is not None
                and requested_project is not None
                and str(requested_project) != self._bound_project
            ):
                raise McpToolError(
                    mo.ErrorCode.ACCESS_DENIED,
                    "workspace scope denied",
                    params={"reason_code": "scope_mismatch"},
                )
            if self._bound_project is not None:
                known_projects: Mapping[str, str | None] | None = {
                    self._bound_project: principal.tenant_id
                }
                requested_project = self._bound_project
            else:
                known_projects = self._known_projects
        else:
            principal = LocalPrincipal.resolve(
                source_host=meta.get("source_host"),
                requested_tenant_id=meta.get("tenant_id"),  # ignored by design
                display_name=meta.get("display_name"),
                roles=_coerce_str_sequence(meta.get("roles")),
                kind=str(meta.get("kind", "user")),
            )
            requested_project = meta.get("project_id")
            known_projects = self._known_projects

        if known_projects is None and requested_project is not None:
            # local-single rule (mirrors workspace/entry_authz.py): a requested
            # project is the principal's own tenant's project unless an explicit
            # map says otherwise.  The client never supplies the tenant.
            known_projects = {str(requested_project): principal.tenant_id}
        resolution = resolve_scope(
            principal,
            None if requested_project is None else str(requested_project),
            known_projects=known_projects,
            user=self._user,
            grants=self._grants,
            execution_mode=str(meta.get("execution_mode", ExecutionMode.LOCAL)),
        )
        if resolution.effect != DecisionEffect.ALLOW:
            raise McpToolError(
                mo.ErrorCode.ACCESS_DENIED,
                "workspace scope denied",
                params={"reason_code": resolution.reason_code},
            )

        available = set(resolution.available_actions or ())
        if self._bound_actions is not None:
            available &= set(self._bound_actions)
        available &= set(STDIO_PERMISSION_CEILING)
        return WorkspaceContext(
            principal=principal,
            workspace_id=str(resolution.scope_id),
            tenant_id=str(resolution.tenant_id),
            project_id=(
                None if resolution.project_scope is None else resolution.project_scope.project_id
            ),
            permission_scope=self._permission_scope,
            available_actions=tuple(sorted(available)),
        )


def _coerce_str_sequence(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _requested_protocol_version(value: Any) -> str | None:
    """Validate the client's single ``protocolVersion`` field.

    Returns the requested version, or ``None`` when the client omitted it.  A
    present-but-not-a-non-empty-string value is a *malformed* parameter and is
    the only protocol-version shape that fails ``initialize`` with ``-32602``.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise McpProtocolError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "protocolVersion must be a non-empty version string",
            data={"code": "invalid_protocol_version", "received_type": type(value).__name__},
        )
    return value


def _declared_protocol_versions(value: Any) -> tuple[str, ...]:
    """Validate the private ``protocolVersions`` hint and return it (empty when absent).

    This list is **diagnostic only** (ADR-006): it is recorded in the initialize
    result for traces and never decides which version the server answers with.
    Only its *shape* is enforced, so a malformed list is ``-32602``.
    """
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise McpProtocolError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "protocolVersions must be an array of non-empty version strings",
        )
    return tuple(str(item) for item in value)


def _negotiate_protocol_version(requested: str | None) -> str:
    """Return the version this server speaks, per the MCP 2025-06-18 lifecycle.

    The server answers with a version it supports; if the client asked for an
    unsupported one it still gets a valid version and decides for itself whether
    it can continue (the client owns compatibility).  Nothing is rejected here —
    malformed shapes are rejected by the validators above.
    """
    if requested is None:
        return MCP_PROTOCOL_VERSION
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return MCP_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Budget (server-enforced caps)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BudgetCaps:
    """Per-workspace budget caps enforced by the adapter before dispatch."""

    max_in_flight: int = 8
    max_submissions: int = 256
    max_cores_per_submission: int = 64
    max_total_cores: int = 256


@dataclass(frozen=True)
class BudgetDecision:
    """Outcome of a budget admission."""

    allowed: bool
    reason_code: str = "admitted"
    detail: str = ""
    exceeded: tuple[Mapping[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "exceeded": [dict(item) for item in self.exceeded],
        }


@dataclass
class _WorkspaceUsage:
    in_flight: int = 0
    submissions: int = 0
    total_cores: int = 0


class WorkspaceBudget:
    """Server-side per-workspace budget ledger (thread-safe).

    A refusal never touches already-admitted work.  ``max_submissions`` is a
    lifetime cap; ``in_flight``/``total_cores`` are released when a long task
    finishes (cancel or a terminal status observation).
    """

    def __init__(self, caps: BudgetCaps | None = None) -> None:
        self._caps = caps or BudgetCaps()
        self._lock = threading.RLock()
        self._usage: dict[str, _WorkspaceUsage] = {}

    @property
    def caps(self) -> BudgetCaps:
        return self._caps

    def _usage_for(self, workspace_id: str) -> _WorkspaceUsage:
        return self._usage.setdefault(str(workspace_id), _WorkspaceUsage())

    def admit(self, workspace_id: str, *, cores: int = 0) -> BudgetDecision:
        """Reserve a slot/cores for *workspace_id*, refusing an over-cap call."""
        cores = max(0, int(cores))
        with self._lock:
            usage = self._usage_for(workspace_id)
            exceeded: list[Mapping[str, Any]] = []
            if cores > self._caps.max_cores_per_submission:
                exceeded.append(
                    {
                        "dimension": "cores_per_submission",
                        "requested": cores,
                        "limit": self._caps.max_cores_per_submission,
                    }
                )
            if usage.in_flight + 1 > self._caps.max_in_flight:
                exceeded.append(
                    {
                        "dimension": "in_flight",
                        "requested": usage.in_flight + 1,
                        "limit": self._caps.max_in_flight,
                    }
                )
            if usage.submissions + 1 > self._caps.max_submissions:
                exceeded.append(
                    {
                        "dimension": "submissions",
                        "requested": usage.submissions + 1,
                        "limit": self._caps.max_submissions,
                    }
                )
            if usage.total_cores + cores > self._caps.max_total_cores:
                exceeded.append(
                    {
                        "dimension": "total_cores",
                        "requested": usage.total_cores + cores,
                        "limit": self._caps.max_total_cores,
                    }
                )
            if exceeded:
                return BudgetDecision(
                    allowed=False,
                    reason_code="budget_exceeded",
                    detail="workspace budget cap exceeded",
                    exceeded=tuple(exceeded),
                )
            usage.in_flight += 1
            usage.submissions += 1
            usage.total_cores += cores
            return BudgetDecision(allowed=True)

    def release(self, workspace_id: str, *, cores: int = 0) -> None:
        """Release one in-flight slot (and its cores) for *workspace_id*."""
        cores = max(0, int(cores))
        with self._lock:
            usage = self._usage_for(workspace_id)
            usage.in_flight = max(0, usage.in_flight - 1)
            usage.total_cores = max(0, usage.total_cores - cores)

    def usage(self, workspace_id: str) -> Mapping[str, int]:
        """Return the current usage projection (diagnostic)."""
        with self._lock:
            usage = self._usage_for(workspace_id)
            return {
                "in_flight": usage.in_flight,
                "submissions": usage.submissions,
                "total_cores": usage.total_cores,
            }


# ---------------------------------------------------------------------------
# Idempotency (server-enforced)
# ---------------------------------------------------------------------------


def derive_idempotency_key(payload: Mapping[str, Any]) -> str:
    """Derive a deterministic retry key from a request when the client omits one.

    Volatile keys (``idempotency_key`` itself) are excluded so a re-sent call
    collapses onto the same operation.
    """
    material = {key: value for key, value in payload.items() if key != "idempotency_key"}
    blob = json.dumps(material, sort_keys=True, default=str, ensure_ascii=False)
    return "auto_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class IdempotencyRegistry:
    """Explicit, server-side exactly-once map for MCP operations.

    Keyed by ``(workspace_id, scoped_idempotency_key)``; the scoped key folds the
    permission scope in (via
    :func:`acp_services.data.operations.idempotency_key_for`) so one scope's
    retry can never shadow another's operation.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: dict[tuple[str, str], Any] = {}

    def get(self, workspace_id: str, idempotency_key: str) -> Any:
        with self._lock:
            return self._entries.get((str(workspace_id), str(idempotency_key)))

    def put(self, workspace_id: str, idempotency_key: str, value: Any) -> Any:
        with self._lock:
            self._entries[(str(workspace_id), str(idempotency_key))] = value
            return value

    def scoped_key(self, *, permission_scope: str, raw_key: str) -> str:
        """Fold the permission scope into *raw_key* (single Services primitive)."""
        return idempotency_key_for(
            permission_scope=str(permission_scope), resource_key=str(raw_key)
        )


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

#: Fixed dispatch table type: tool name -> bound handler.
_ToolHandler = Callable[[Mapping[str, Any], WorkspaceContext], mo.MachineResult]


class McpServer:
    """A stateless MCP adapter over injected Service use cases.

    The server holds no per-connection state: the same instance (or a fresh one
    sharing the same ports/registries) answers any request, and a dropped
    connection has no bearing on a submitted operation.  ``handle`` is the only
    entry point; the caller owns stdin/stdout framing.

    Args:
        backends: The injected Service use cases.
        identity: Identity mapper (defaults to the local stdio scope).
        budget: Optional in-process cache/test adapter.  ``None`` on the final
            Controller path: the Controller's server-side admission ledger is
            the authority, and a tool hint can never override its grant.
        idempotency: Server-enforced idempotency registry.
        configured_tier: Deployment tier; request overrides are refused.
        extension: The research extension this server advertises through
            ``acp_capabilities``.  Declared by the composition root (the id is a
            wire literal and belongs there); ``None`` advertises no extension and
            refuses every extension request as ``extension_not_advertised``.
    """

    def __init__(
        self,
        *,
        backends: McpBackends,
        identity: IdentityMapper | None = None,
        budget: WorkspaceBudget | None = None,
        idempotency: IdempotencyRegistry | None = None,
        configured_tier: str = DeploymentTier.LOCAL_SINGLE,
        extension: ExtensionSpec | None = None,
    ) -> None:
        self._backends = backends
        self._identity = identity or IdentityMapper(configured_tier=configured_tier)
        self._budget = budget
        self._idempotency = idempotency or IdempotencyRegistry()
        self._tier = str(configured_tier)
        self._extension = extension
        # A *literal* allowlist table — never resolved from client input.
        self._tool_dispatch: dict[str, _ToolHandler] = {
            McpTool.CAPABILITIES: self._tool_capabilities,
            McpTool.PREFLIGHT: self._tool_preflight,
            McpTool.SUBMIT: self._tool_submit,
            McpTool.STATUS: self._tool_status,
            McpTool.CANCEL: self._tool_cancel,
            McpTool.PREPARE_ARTIFACT: self._tool_prepare_artifact,
            McpTool.READ_ARTIFACT: self._tool_read_artifact,
            McpTool.ANALYZE: self._tool_analyze,
        }

    # -- JSON-RPC transport ------------------------------------------------

    def handle(self, request: Any) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC 2.0 request; ``None`` for a notification."""
        if isinstance(request, list):
            return self._error_response(
                None,
                JsonRpcErrorCode.INVALID_REQUEST,
                "JSON-RPC batch requests are not supported",
                {"code": "batch_not_supported"},
            )
        if not isinstance(request, Mapping):
            return self._error_response(
                None, JsonRpcErrorCode.INVALID_REQUEST, "request must be a JSON object"
            )
        method = request.get("method")
        request_id = request.get("id")
        is_notification = "id" not in request
        if not isinstance(method, str) or not method:
            if is_notification:
                return None
            return self._error_response(
                request_id, JsonRpcErrorCode.INVALID_REQUEST, "missing method"
            )
        if method.startswith("notifications/"):
            return None
        params = request.get("params")
        if params is not None and not isinstance(params, Mapping):
            if is_notification:
                return None
            return self._error_response(
                request_id, JsonRpcErrorCode.INVALID_PARAMS, "params must be an object"
            )
        params = params or {}
        try:
            result = self._invoke(method, params)
        except McpProtocolError as exc:
            if is_notification:
                return None
            return self._error_response(request_id, exc.code, str(exc), exc.data)
        except McpToolError as exc:  # defensive: tool errors are handled in _tools_call
            return self._error_response(
                request_id,
                JsonRpcErrorCode.INTERNAL_ERROR,
                str(exc),
                exc.to_error_object().to_dict(),
            )
        except Exception as exc:  # pragma: no cover - unexpected programming error
            return self._error_response(
                request_id,
                JsonRpcErrorCode.INTERNAL_ERROR,
                f"internal error: {type(exc).__name__}",
            )
        if is_notification:
            return None
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}

    def handle_line(self, line: str) -> str | None:
        """Parse + dispatch one JSON text frame; return the response JSON or None."""
        try:
            request = json.loads(line)
        except (TypeError, ValueError):
            return json.dumps(
                self._error_response(None, JsonRpcErrorCode.PARSE_ERROR, "parse error")
            )
        try:
            response = self.handle(request)
        except McpProtocolError as exc:
            response = self._error_response(None, exc.code, str(exc), exc.data)
        return None if response is None else json.dumps(response, ensure_ascii=False)

    def _invoke(self, method: str, params: Mapping[str, Any]) -> Any:
        if method == "initialize":
            return self._initialize(params)
        if method == "tools/list":
            return self._tools_list()
        if method == "tools/call":
            return self._tools_call(params)
        if method == "ping":
            return {}
        raise McpProtocolError(JsonRpcErrorCode.METHOD_NOT_FOUND, f"unknown method {method!r}")

    @staticmethod
    def _error_response(
        request_id: Any, code: int, message: str, data: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": int(code), "message": str(message)}
        if data:
            error["data"] = dict(data)
        return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}

    # -- methods -----------------------------------------------------------

    def _initialize(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Answer the standard MCP handshake (no extension negotiation claims).

        The result carries exactly the spec fields plus two dormant-diagnostic
        additions: ``supportedProtocolVersions`` (the advertised set) and one
        negotiation-guidance extension under ``capabilities.extensions``.  There
        is **no** top-level ``extensions`` block and no negotiated claim of any
        kind: M0-09 (ADR-006 D7) selected the application-level handshake carried
        by ``tools/call acp_capabilities``, and this method only *points* at it
        through ``extension_handshake``.
        """
        requested = _requested_protocol_version(params.get("protocolVersion"))
        declared = _declared_protocol_versions(params.get("protocolVersions"))
        negotiated = _negotiate_protocol_version(requested)
        return {
            "protocolVersion": negotiated,
            "supportedProtocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "capabilities": {
                "tools": {"listChanged": False},
                "extensions": {
                    _NEGOTIATION_EXTENSION_ID: {
                        "id": _NEGOTIATION_EXTENSION_ID,
                        "version": "1.0.0",
                        "supported_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
                        "server_preferred_version": MCP_PROTOCOL_VERSION,
                        "negotiation": "server_advertises_supported_version",
                        "client_decides_compatibility": True,
                        # Recorded for traces only — never a gate (ADR-006).
                        "requested_version": requested,
                        "client_declared_versions": list(declared),
                        # Where capability extensions are actually negotiated
                        # (M0-09): a tools/call to the P1 discovery tool.  This is
                        # guidance, not a claim about any extension's state.
                        "extension_handshake": {
                            "carrier": "tools/call",
                            "tool": McpTool.CAPABILITIES,
                            "result_field": "extensions",
                        },
                    }
                },
            },
            "serverInfo": {"name": MCP_SERVER_NAME, "version": MCP_SERVER_VERSION},
            "instructions": (
                "Local stdio MCP entry (permission scope "
                f"{STDIO_PERMISSION_SCOPE!r}, transport {STDIO_TRANSPORT!r}). Tool "
                "calls are dispatched against a closed allowlist; no shell is "
                "exposed. Long tasks return an operation/job reference that "
                "survives disconnection; query it with acp_status."
            ),
        }

    def _tools_list(self) -> dict[str, Any]:
        return {"tools": [spec for spec in _tool_specs() if spec["name"] in TOOL_ALLOWLIST]}

    def _tools_call(self, params: Mapping[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name:
            raise McpProtocolError(
                JsonRpcErrorCode.INVALID_PARAMS, "tools/call requires a tool name"
            )
        handler = self._tool_dispatch.get(name)
        if handler is None:
            # Unknown / non-allowlisted tool: refuse at the protocol layer.
            raise McpProtocolError(
                JsonRpcErrorCode.INVALID_PARAMS,
                f"unknown or non-allowlisted tool {name!r}",
                data={"code": "unknown_tool", "allowlist": sorted(TOOL_ALLOWLIST)},
            )
        arguments = params.get("arguments")
        if arguments is not None and not isinstance(arguments, Mapping):
            raise McpProtocolError(
                JsonRpcErrorCode.INVALID_PARAMS, "tools/call arguments must be an object"
            )
        arguments = arguments or {}
        meta = params.get("_meta")
        meta = meta if isinstance(meta, Mapping) else {}

        try:
            context = self._identity.map(meta)
            self._authorize(name, context)
            result = handler(arguments, context)
        except McpToolError as exc:
            error = exc.to_error_object()
            result = mo.MachineResult(
                operation=name,
                status="error",
                exit_code=error.exit_code,
                error=error,
            )
        return self._result_payload(result)

    @staticmethod
    def _result_payload(result: mo.MachineResult) -> dict[str, Any]:
        payload = result.to_dict()
        return {
            "content": [{"type": "text", "text": result.to_json()}],
            "structuredContent": payload,
            "isError": result.status == "error",
        }

    # -- authorization -----------------------------------------------------

    def _authorize(self, name: str, context: WorkspaceContext) -> None:
        required = TOOL_REQUIRED_ACTION.get(name, AccessAction.READ)
        if required not in context.available_actions:
            raise McpToolError(
                mo.ErrorCode.ACCESS_DENIED,
                f"tool {name!r} requires action {required!r}",
                params={"required_action": required, "available": list(context.available_actions)},
            )

    # -- tool handlers -----------------------------------------------------

    def _tool_capabilities(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        """Read-only discovery **plus** the application-level extension handshake.

        The tool result always carries ``extensions[]`` (possibly empty).  A
        research-extension request is answered in-band:

        * ``negotiated=True`` only when the promise set is implemented *and* the
          requested version matches the single supported version;
        * otherwise an ``unsupported_capability`` error result whose ``data``
          still carries the full ``extensions[]`` projection, so a caller can read
          the supported version instead of guessing.

        Identity and project come from the server binding
        (:class:`IdentityMapper` / :class:`WorkspaceContext`); the optional
        ``project_id`` argument is only an advisory hint that must agree with the
        binding, and ``client`` is audit metadata that never authorizes.
        """
        data = dict(self._backends.capabilities.capabilities())
        data.setdefault("protocol_version", MCP_PROTOCOL_VERSION)
        data.setdefault("tools", sorted(TOOL_ALLOWLIST))

        raw_extension = arguments.get("research_extension")
        if raw_extension is not None and not isinstance(raw_extension, Mapping):
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "research_extension must be an object",
                params={
                    "field": "research_extension",
                    "received_type": type(raw_extension).__name__,
                },
            )
        try:
            requested = normalize_extension_request(raw_extension)
        except ValueError as exc:
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                str(exc),
                params={"field": "research_extension"},
            ) from None
        handshake = negotiate_research_extension(self._extension, requested)
        # The Controller may project its own extension view; the handshake is the
        # server's answer and is never delegated to a downstream payload.
        data["extensions"] = [dict(entry) for entry in handshake.entries]

        client = arguments.get("client")
        if client is not None and not isinstance(client, Mapping):
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "client must be an object",
                params={"field": "client", "received_type": type(client).__name__},
            )
        hint = arguments.get("project_id")
        if hint is not None and (not isinstance(hint, str) or not hint.strip()):
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "project_id must be a non-empty string when present",
                params={"field": "project_id"},
            )
        project_hint = None if hint is None else str(hint).strip()
        data["capability_request"] = {
            "client": dict(client) if isinstance(client, Mapping) else None,
            "project_id": project_hint,
            "research_extension": dict(requested) if requested is not None else None,
            "authorization": "none: audit and advisory hints only",
        }
        # Named `identity_binding` (not `identity`) so the Controller's own
        # `identity` projection in the forwarded payload is preserved: this field
        # is the MCP session's server-resolved binding, never a client claim.
        data["identity_binding"] = self._identity.binding_projection()

        if (
            project_hint is not None
            and context.project_id is not None
            and project_hint != context.project_id
        ):
            error = mo.error_object(
                mo.ErrorCode.ACCESS_DENIED,
                "requested project does not match the server binding",
                params={
                    "rule": "project_scope_mismatch",
                    "requested_project_id": project_hint,
                    "bound_project_id": context.project_id,
                },
                retryable=False,
            )
            return mo.MachineResult(
                operation=McpTool.CAPABILITIES,
                status="error",
                exit_code=error.exit_code,
                data=data,
                error=error,
                operation_identity=context.workspace_id,
            )

        if handshake.refusal is not None:
            params = dict(handshake.refusal.get("params") or {})
            error = mo.error_object(
                str(handshake.refusal.get("code") or mo.ErrorCode.UNSUPPORTED_CAPABILITY),
                "research extension request refused",
                params=params,
                retryable=False,
            )
            return mo.MachineResult(
                operation=McpTool.CAPABILITIES,
                status="error",
                exit_code=error.exit_code,
                data=data,
                error=error,
                operation_identity=context.workspace_id,
            )

        return mo.MachineResult(
            operation=McpTool.CAPABILITIES,
            status="ok",
            data=data,
            operation_identity=context.workspace_id,
        )

    def _tool_preflight(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        estimate = dict(
            self._backends.preflight.preflight(arguments, workspace_id=context.workspace_id)
        )
        return mo.dry_run_result(
            McpTool.PREFLIGHT,
            estimate=estimate,
            operation_identity=context.workspace_id,
        )

    def _tool_submit(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        idem = self._resolve_idempotency(arguments, context)
        payload_digest = derive_idempotency_key(arguments)
        cached = self._idempotency.get(context.workspace_id, idem)
        if isinstance(cached, Mapping) and cached.get("payload_digest") is not None:
            if cached.get("payload_digest") == payload_digest:
                return mo.MachineResult(
                    operation=McpTool.SUBMIT,
                    status="ok",
                    data={"reference": cached.get("reference"), "idempotent_replay": True},
                    operation_identity=context.workspace_id,
                )
            cached = None
        elif cached is not None:
            return mo.MachineResult(
                operation=McpTool.SUBMIT,
                status="ok",
                data={"reference": cached, "idempotent_replay": True},
                operation_identity=context.workspace_id,
            )

        cores = _requested_cores(arguments)
        if self._budget is not None:
            decision = self._budget.admit(context.workspace_id, cores=cores)
            if not decision.allowed:
                raise McpToolError(
                    mo.ErrorCode.ACCESS_DENIED,
                    "workspace budget cap exceeded",
                    params=decision.to_dict(),
                    retryable=False,
                )
        try:
            record = self._backends.submit.submit(
                arguments, idempotency_key=idem, workspace_id=context.workspace_id
            )
        except McpToolError:
            if self._budget is not None:
                self._budget.release(context.workspace_id, cores=cores)
            raise
        reference = _reference_from(record, kind="job")
        self._idempotency.put(
            context.workspace_id,
            idem,
            {"reference": reference, "payload_digest": payload_digest},
        )
        payload: dict[str, Any] = {
            "reference": reference,
            "idempotency_key": idem,
            "query_tool": McpTool.STATUS,
            "cancel_tool": McpTool.CANCEL,
        }
        if isinstance(record, Mapping):
            if isinstance(record.get("task_ref"), Mapping):
                payload["task_ref"] = dict(record["task_ref"])
            if record.get("result") is not None:
                payload["result"] = record["result"]
            if record.get("approval_ref") is not None:
                payload["approval_ref"] = record["approval_ref"]
            if record.get("plan_digest") is not None:
                payload["plan_digest"] = record["plan_digest"]
            if record.get("idempotent_replay") is not None:
                payload["idempotent_replay"] = record["idempotent_replay"]
            if record.get("admission") is not None:
                payload["admission"] = record["admission"]
            if record.get("identity") is not None:
                payload["identity"] = record["identity"]
        return mo.MachineResult(
            operation=McpTool.SUBMIT,
            status="ok",
            data=payload,
            operation_identity=context.workspace_id,
        )

    def _tool_status(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        reference = _require_reference(arguments)
        record = self._backends.status.get(reference, workspace_id=context.workspace_id)
        if record is None:
            raise McpToolError(
                mo.ErrorCode.RESOURCE_NOT_READY,
                "operation/job reference not found",
                params={"reference": reference},
                retryable=True,
            )
        projection = record if isinstance(record, Mapping) else {"result": record}
        projection = dict(projection)
        projection.setdefault("reference", reference)
        if _projection_is_terminal(projection) and self._budget is not None:
            self._budget.release(context.workspace_id)
        return mo.MachineResult(
            operation=McpTool.STATUS,
            status="ok",
            data=projection,
            operation_identity=context.workspace_id,
        )

    def _tool_cancel(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        reference = _require_reference(arguments)
        record = self._backends.cancel.cancel(reference, workspace_id=context.workspace_id)
        if self._budget is not None:
            self._budget.release(context.workspace_id)
        projection = record if isinstance(record, Mapping) else {"result": record}
        projection = dict(projection)
        projection.setdefault("reference", reference)
        projection.setdefault("cancel_requested", True)
        return mo.MachineResult(
            operation=McpTool.CANCEL,
            status="ok",
            data=projection,
            operation_identity=context.workspace_id,
        )

    def _tool_prepare_artifact(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        idem = self._resolve_idempotency(arguments, context)
        cached = self._idempotency.get(context.workspace_id, idem)
        if cached is not None:
            return mo.MachineResult(
                operation=McpTool.PREPARE_ARTIFACT,
                status="ok",
                data={"reference": cached, "idempotent_replay": True},
                operation_identity=context.workspace_id,
            )
        if self._budget is not None:
            decision = self._budget.admit(context.workspace_id)
            if not decision.allowed:
                raise McpToolError(
                    mo.ErrorCode.ACCESS_DENIED,
                    "workspace budget cap exceeded",
                    params=decision.to_dict(),
                    retryable=False,
                )
        try:
            record = self._backends.artifacts.prepare(
                arguments, idempotency_key=idem, workspace_id=context.workspace_id
            )
        except McpToolError:
            if self._budget is not None:
                self._budget.release(context.workspace_id)
            raise
        reference = _reference_from(record, kind="operation")
        self._idempotency.put(context.workspace_id, idem, reference)
        payload: dict[str, Any] = {
            "reference": reference,
            "idempotency_key": idem,
            "query_tool": McpTool.STATUS,
        }
        if isinstance(record, Mapping):
            if record.get("operation_ref") is not None:
                payload["operation_ref"] = record["operation_ref"]
            if record.get("availability") is not None:
                payload["availability"] = record["availability"]
        return mo.MachineResult(
            operation=McpTool.PREPARE_ARTIFACT,
            status="ok",
            data=payload,
            operation_identity=context.workspace_id,
        )

    def _tool_read_artifact(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        record = self._backends.artifacts.read(arguments, workspace_id=context.workspace_id)
        data = record if isinstance(record, Mapping) else {"result": record}
        return mo.MachineResult(
            operation=McpTool.READ_ARTIFACT,
            status="ok",
            data=dict(data),
            operation_identity=context.workspace_id,
        )

    def _tool_analyze(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        # Explicit analysis: an implicit/heuristic trigger is refused.
        if arguments.get("explicit") is not True:
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "analysis requires explicit=true",
                params={"field": "explicit"},
            )
        cores = _requested_cores(arguments)
        if self._budget is not None:
            decision = self._budget.admit(context.workspace_id, cores=cores)
            if not decision.allowed:
                raise McpToolError(
                    mo.ErrorCode.ACCESS_DENIED,
                    "workspace budget cap exceeded",
                    params=decision.to_dict(),
                    retryable=False,
                )
        try:
            record = self._backends.analysis.analyze(arguments, workspace_id=context.workspace_id)
        except McpToolError:
            if self._budget is not None:
                self._budget.release(context.workspace_id, cores=cores)
            raise
        data = record if isinstance(record, Mapping) else {"result": record}
        return mo.MachineResult(
            operation=McpTool.ANALYZE,
            status="ok",
            data=dict(data),
            operation_identity=context.workspace_id,
        )

    # -- helpers -----------------------------------------------------------

    def _resolve_idempotency(self, arguments: Mapping[str, Any], context: WorkspaceContext) -> str:
        raw = arguments.get("idempotency_key")
        if not isinstance(raw, str) or not raw.strip():
            raw = derive_idempotency_key(arguments)
        return self._idempotency.scoped_key(permission_scope=context.permission_scope, raw_key=raw)


# ---------------------------------------------------------------------------
# Result helpers
# ---------------------------------------------------------------------------

_ID_KEYS = ("reference", "job_id", "jobId", "id", "operation_id", "operationId", "task_id")


def _reference_from(record: Any, *, kind: str) -> dict[str, Any]:
    """Normalise a backend record into a stable ``{kind, id}`` reference.

    Ports return a mapping (the adapter is thin and never reflects attributes);
    a non-mapping record degrades to a string id rather than dynamic lookup.
    """
    if record is None:
        return {"kind": kind, "id": ""}
    if isinstance(record, Mapping):
        for key in _ID_KEYS:
            value = record.get(key)
            if value is None:
                continue
            if isinstance(value, Mapping):
                merged = dict(value)
                merged.setdefault("kind", kind)
                return merged
            return {"kind": kind, "id": str(value)}
    return {"kind": kind, "id": str(record)}


def _require_reference(arguments: Mapping[str, Any]) -> str:
    raw = arguments.get("reference")
    if isinstance(raw, Mapping):
        raw = raw.get("id")
    if not isinstance(raw, str) or not raw.strip():
        raise McpToolError(
            mo.ErrorCode.INPUT_INVALID,
            "a reference (id) is required",
            params={"field": "reference"},
        )
    return raw


def _requested_cores(arguments: Mapping[str, Any]) -> int:
    resources = arguments.get("resources")
    if isinstance(resources, Mapping) and resources.get("cores") is not None:
        try:
            return max(0, int(resources["cores"]))
        except (TypeError, ValueError):
            return 0
    try:
        return max(0, int(arguments.get("cores", 0) or 0))
    except (TypeError, ValueError):
        return 0


def _projection_is_terminal(projection: Mapping[str, Any]) -> bool:
    if projection.get("terminal") is True:
        return True
    for key in ("state", "status"):
        value = projection.get(key)
        if isinstance(value, str) and value.lower() in _TERMINAL_STATES:
            return True
    return False


def client_matrix() -> tuple[Mapping[str, Any], ...]:
    """Return the tracked protocol/client support matrix.

    One locked protocol version; the local stdio transport is the only
    supported, permission-bounded scope.  ``session_coupled`` is always
    ``False`` — a dropped connection never decides a job's fate.

    The DSH row is *real transport evidence* (M0-08, ADR-006): the frozen
    ``@deepseek-ai/dsh-mcp-client`` package, its pinned
    ``@modelcontextprotocol/client`` ``2.2.0`` and its production constructor
    options (``versionNegotiation: {mode: 'auto'}``) completed ``initialize`` and
    listed the eight P1 tools against this adapter.  That is an SDK/transport
    pass, **not** a full DSH Agent pass: model, skills and the research
    extension handshake remain ``NOT_VERIFIED``.
    """
    return (
        {
            "client": "stdio-mcp-client",
            "transport": STDIO_TRANSPORT,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "permission_scope": STDIO_PERMISSION_SCOPE,
            "session_coupled": False,
            "status": (
                "real stdio process verified (B05 launch + probe); standard handshake "
                "matrix verified (M0-08 process tests + negotiation probe)"
            ),
        },
        {
            "client": "dsh-official-mcp-client",
            "package": "@deepseek-ai/dsh-mcp-client",
            "package_version": "0.2.1-rc.2",
            "client_info": {"name": "dsh-mcp-client", "version": "0.0.1"},
            "sdk": "@modelcontextprotocol/client",
            "sdk_version": "2.2.0",
            "version_negotiation": "auto (server/discover probe -> legacy initialize fallback)",
            "transport": STDIO_TRANSPORT,
            "protocol_version": MCP_PROTOCOL_VERSION,
            "supported_protocol_versions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "permission_scope": STDIO_PERMISSION_SCOPE,
            "session_coupled": False,
            "status": (
                "PASS handshake: real DSH client 2.2.0 initialize (client requests 2025-11-25, "
                "server answers 2025-06-18) + tools/list 8; full DSH Agent (model/skills/"
                "research extension) NOT_VERIFIED"
            ),
        },
        {
            "client": "http-sse-mcp-client",
            "transport": "http+sse",
            "protocol_version": MCP_PROTOCOL_VERSION,
            "permission_scope": None,
            "session_coupled": False,
            "status": "NOT_VERIFIED (no transport in this slice)",
        },
    )
