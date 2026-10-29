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
* ``tools/list`` advertises a **closed allowlist**: the eight frozen P1 tools,
  plus the ten research tools **only after this session negotiated the research
  extension** (M2-01c).  ``tools/call`` dispatches through a *literal* table keyed
  by those names.  There is **no** arbitrary shell, no
  ``getattr``/``eval``/``exec``/``importlib`` function-name dispatch and no
  path/command string ever reaches a subprocess.
* **Application-level extension handshake (M0-09 / ADR-006 D7).**  ``initialize``
  makes no extension claim of any kind; a capability extension is negotiated with
  a ``tools/call`` to :data:`McpTool.CAPABILITIES`, whose result always carries
  ``extensions[]`` (``id``/``version``/``supported``/``requested``/
  ``negotiated_by``/``implemented``/``negotiated``/``refusal``).  Advertising an
  extension id is **not** implementing it: ``implemented`` is *derived* from the
  registered set covering the whole promise set (:data:`RESEARCH_TOOL_NAMES`,
  ten names — registered since M2-01c), and ``negotiated`` additionally requires
  this session to have asked.  A research call from an unnegotiated session is an
  in-band ``unsupported_capability`` result (``rule=extension_not_negotiated``),
  never a transport error and never a silently accepted claim.
* **Identity is server-enforced.**  The client may only *hint* a project id; the
  tenant/tier/principal are derived from the verified local principal
  (:func:`acp_services.contracts.access.resolve_scope`) and any client-supplied
  tenant or deployment tier is ignored (tier overrides are refused outright).
* **One idempotency authority (M0-05).**  There is no MCP-side result cache and
  no second ledger: the client's ``idempotency_key`` is forwarded **unchanged**
  to the Controller, whose persistent ``idempotency_records`` ledger
  (:meth:`JobStore.claim_idempotency
  <acp_services.store.store.JobStore.claim_idempotency>`) owns the claim.  The
  permission scope no longer folds into the key and an ``op_``-shaped key is
  never written as a ledger key (``op_`` is the *operation reference*
  namespace).  **M1-01b: ``acp_submit`` requires the key** — a missing/blank key
  is a typed ``input_invalid`` (``params.rule=idempotency_key_required``) with
  zero side effects, and the only surviving ``auto_`` path is the frozen legacy
  lookup below (historical rows and the optional-key prepare surface).
* **Declared argument tables are enforced here (M1-01b).**  ``tools/list``
  advertises the same ``additionalProperties: false`` tables the adapter checks
  before any port call (``_ARGUMENT_SCHEMAS``): an unknown execution field is
  ``input_invalid``/``unknown_field``, a missing required field is
  ``input_invalid``/``missing_required_field``.  Status stays a permissive
  historical read (unknown-field policy U2).
* **One reviewed wire-id station (M1-01c).**  The only ``acp.*`` wire literal in
  this package is the negotiation-guidance id declared in
  :mod:`acp_services.entrypoints.wire_identifiers`; this module refers to the
  constant and never repeats the literal (the old ``chr(97)`` assembly is gone).
* **Frozen legacy ``auto_`` lookup.**  Pre-M0-05 MCP clients that sent no key
  were recorded under :func:`derive_idempotency_key`'s ``auto_`` namespace.  The
  derivation is now *lookup-only*: it is recomputed only on the explicit
  ``local-single`` operator binding (the only writer of that namespace) so an
  old client's retry replays the original task instead of creating a second
  one.  The Controller's ledger enforces the R09 rules: only a
  ``migration_state == "legacy-verified"`` row replays; a
  ``legacy-pending``/``legacy-unverified`` row is a typed conflict to
  reconcile.  An authenticated binding never claims that namespace.
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
or forwarded to a tool.  A multi-user launch (:data:`STDIO_MULTIUSER_SCOPE`) is
bound to a credential the Controller resolved; the binding is **revalidated on
every tool call** (M0-05), so a revoked key stops reads, writes and control
without needing a reconnect.

Dependency direction
--------------------
``src/acp_services/**`` must never import ``acp`` (see
``tests/baseline/services-evidence/public_allowlist.md``).  This module imports
only the stdlib plus the stdlib-only Services seams
(:mod:`acp_services.contracts.access`,
:mod:`acp_services.entrypoints.machine_output`,
:mod:`acp_services.workspace.boundary`,
:mod:`acp_services.workspace.command_identity`).  The composition root on the
legacy side injects the concrete ports; this module never reaches into the
CLI/API.

Author: ACP Services (B lane, todo 43; M0-05 idempotency/identity wiring).
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
from acp_services.entrypoints import machine_output as mo
from acp_services.entrypoints import wire_identifiers
from acp_services.entrypoints.application_params import (
    ANALYZE_REQUEST_FIELDS,
    ANALYZE_REQUEST_REQUIRED,
    MAX_READ_BYTES,
    ApplicationParamError,
    normalize_artifact_ref,
)
from acp_services.entrypoints.research_consumption import (
    CONSUMPTION_ACTIONS,
    CONSUMPTION_SCHEMAS,
    CONSUMPTION_TOOL_NAMES,
    CONSUMPTION_VERSION,
    ConsumptionPort,
)
from acp_services.runtime.analysis_capabilities import (
    AnalysisCapabilityRegistry,
    capability_projection,
)
from acp_services.workspace.boundary import evaluate_tier_request
from acp_services.workspace.command_identity import (
    CommandIdentity,
    EntryClass,
    PermissionScope,
)

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
    "RESEARCH_TOOL_OPERATIONS",
    "RESEARCH_TOOL_SET",
    "RESEARCH_TOOL_FAMILIES",
    "RESEARCH_TOOL_REQUIRED_ACTION",
    "REGISTERED_TOOL_NAMES",
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
    "ResearchPort",
    "McpBackends",
    # Identity
    "WorkspaceContext",
    "IdentityMapper",
    "StdioCredentialBinding",
    "STDIO_CREDENTIAL_BINDING_SCHEMA",
    # Budget
    "BudgetCaps",
    "BudgetDecision",
    "WorkspaceBudget",
    # Idempotency (M0-05: one authority, raw key passthrough)
    "IdempotencyResolution",
    "IDEMPOTENCY_KEY_ORIGIN_CLIENT",
    "IDEMPOTENCY_KEY_ORIGIN_LEGACY",
    "IDEMPOTENCY_KEY_ORIGIN_NONE",
    "LEGACY_DERIVED_KEY_PREFIX",
    "LEGACY_DERIVED_KEY_VERSION",
    "derive_idempotency_key",
    # Server
    "McpServer",
    "client_matrix",
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
#: as a ``negotiated`` claim).  The value lives in
#: :mod:`acp_services.entrypoints.wire_identifiers`, the single reviewed station
#: for ``acp.*`` wire literals on the Services side (M1-01c); this module refers
#: to the constant and never repeats the literal.
_NEGOTIATION_EXTENSION_ID = wire_identifiers.NEGOTIATION_EXTENSION_ID


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
#: P2-R1…P2-R5, ten unique names).  M2-01c registers all ten, so the extension
#: reports ``implemented=True`` and ``negotiated=True`` for a matching version —
#: but only :attr:`McpServer.research_tools_visible` (negotiated session) puts them
#: in ``tools/list``.  P1's eight names stay in :data:`TOOL_ALLOWLIST` unchanged.
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

#: Tool name → P2 operation.  The M2-01c guard asserts this map is exactly the ten
#: promised names and that every operation has a ``ControllerClient`` method
#: (``P2_OPERATION_METHODS``), so a live tool can never exist without transport.
RESEARCH_TOOL_OPERATIONS: dict[str, str] = {
    "acp_research_proposal_create_v1": "proposal_create",
    "acp_research_proposal_read_v1": "proposal_read",
    "acp_research_proposal_update_cas_v1": "proposal_update_cas",
    "acp_research_approval_request_v1": "approval_request",
    "acp_research_approval_query_v1": "approval_query",
    "acp_research_approval_decide_v1": "approval_decide",
    "acp_research_approval_revoke_v1": "approval_revoke",
    "acp_research_wait_v1": "wait",
    "acp_research_snapshot_v1": "snapshot",
    "acp_research_resume_v1": "resume",
}

#: The registered research tool set (the *implementation*, not the promise).
RESEARCH_TOOL_SET: frozenset[str] = frozenset(RESEARCH_TOOL_NAMES)

#: Every tool the adapter can dispatch.  ``implemented`` is derived from this set
#: covering the whole promise set — never asserted by a caller.
REGISTERED_TOOL_NAMES: frozenset[str] = (
    frozenset(TOOL_ALLOWLIST) | RESEARCH_TOOL_SET | frozenset(CONSUMPTION_TOOL_NAMES)
)

#: Family label per research tool (documentation / client matrix).
RESEARCH_TOOL_FAMILIES: dict[str, str] = {
    "acp_research_proposal_create_v1": "proposal_read_write",
    "acp_research_proposal_read_v1": "proposal_read_write",
    "acp_research_proposal_update_cas_v1": "proposal_read_write",
    "acp_research_approval_request_v1": "approval_request_query",
    "acp_research_approval_query_v1": "approval_request_query",
    "acp_research_approval_decide_v1": "approval_decision",
    "acp_research_approval_revoke_v1": "approval_decision",
    "acp_research_wait_v1": "bounded_wait_snapshot",
    "acp_research_snapshot_v1": "bounded_wait_snapshot",
    "acp_research_resume_v1": "research_resume",
}

#: Access action each research tool requires (enforced against the resolved scope).
RESEARCH_TOOL_REQUIRED_ACTION: dict[str, str] = {
    "acp_research_proposal_create_v1": AccessAction.WRITE,
    "acp_research_proposal_read_v1": AccessAction.READ,
    "acp_research_proposal_update_cas_v1": AccessAction.WRITE,
    "acp_research_approval_request_v1": AccessAction.WRITE,
    "acp_research_approval_query_v1": AccessAction.READ,
    "acp_research_approval_decide_v1": AccessAction.CONTROL,
    "acp_research_approval_revoke_v1": AccessAction.CONTROL,
    "acp_research_wait_v1": AccessAction.READ,
    "acp_research_snapshot_v1": AccessAction.READ,
    "acp_research_resume_v1": AccessAction.WRITE,
}

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
    registered: Mapping[str, Any] | frozenset[str] | set[str] = REGISTERED_TOOL_NAMES,
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
       ``extension_not_implemented``.  Since M2-01c the default registered set
       (:data:`REGISTERED_TOOL_NAMES`) covers the ten promised tools, so this
       refusal only answers a *partial* registration.
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
        "workflow": {
            "type": "string",
            "description": (
                "Optional workflow id (case-insensitive). Returns workflow_contract "
                "with the input/method/resources schema, examples and known "
                "constraints. Unknown ids return input_invalid with allowed ids "
                "and a suggestion; other active workflows are marked coverage="
                "not_provided."
            ),
        },
    },
    "additionalProperties": False,
}
#: Shared declared field table of the submit family.  The names are the
#: ``acp_services.entrypoints.application_params.SUBMIT_FIELD_POLICY`` set (minus
#: the two server-path fields a client may never choose), so the adapter's
#: ``tools/list`` schema and the Controller's field policy are one table
#: (M0-06/M1-01b).  ``additionalProperties`` is false everywhere: an undeclared
#: argument is a typed ``input_invalid`` at the adapter, never forwarded.
_SUBMIT_PROPERTIES: dict[str, Any] = {
    "schema_version": {
        "type": "integer",
        "enum": [1],
        "default": 1,
        "description": "Contract major version; any other value executes nothing.",
    },
    "workflow": {"type": "string", "description": "Active workflow id (server-validated)."},
    "name": {"type": "string", "description": "Display name (never the task directory)."},
    "input": {"type": "object", "description": "Workflow input descriptor."},
    "method": {"type": "object", "description": "Method/parameter descriptor."},
    "resources": {"type": "object", "description": "Optional resource request (e.g. cores)."},
    "tags": {"type": "array", "items": {"type": "string"}, "description": "Filter tags."},
    "node_tags": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Required node capability tags (AND semantics).",
    },
    "project_id": {
        "type": "string",
        "description": (
            "Requested Workspace hint (never the tenant). Legacy local submissions "
            "that omit it land in the server default project (uncategorized)."
        ),
    },
    "execution_mode": {
        "type": "string",
        "enum": ["local", "remote"],
        "description": "Per-request execution preference.",
    },
    "target_node": {
        "type": "string",
        "description": "Pin one execution target ('local' or a configured remote node).",
    },
    "molecule_name": {"type": "string", "description": "v2 task-directory molecule part."},
    "task_name": {"type": "string", "description": "v2 task-directory task part."},
    "remark": {"type": "string", "description": "v2 task-directory remark part."},
    "preflight_fingerprint": {
        "type": "string",
        "description": "Optional binding to the plan digest a preflight returned.",
    },
    "idempotency_key": {
        "type": "string",
        "description": (
            "REQUIRED. Client retry key, forwarded unchanged to the Controller ledger. "
            "The raw key is a global namespace per action (across projects/users): use a "
            "high-entropy UUID or a namespaced key (e.g. "
            "'<client>:<project>:<round>:<call>'). A short human key reused by two "
            "tenants is a typed conflict (revision_conflict), never a replay. Same key + "
            "same verified identity + same request = exactly one operation. Omitting it "
            "is refused with input_invalid (rule=idempotency_key_required)."
        ),
    },
    "extensions": {
        "type": "object",
        "description": "Namespaced extension metadata (never used for execution).",
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
}

_SUBMIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Submit descriptor. Legacies and research launches share this schema; the field "
        "relations below are documented for callers, never enforced here — the Controller "
        "remains the gate. idempotency_key is required: an undeclared field is a "
        "typed input_invalid (rule=unknown_field) and a missing key is "
        "input_invalid (rule=idempotency_key_required) before any budget or Controller "
        "call. plan_digest is the research-launch switch: supplying it (or any of "
        "proposal_ref/proposal_digest/budget) makes the submit a research launch, which "
        "additionally requires proposal_ref bound to a server-side approval and optionally "
        "proposal_digest/budget. Omitting all four keeps the legacy path. A research submit "
        "without proposal_ref is refused by the Controller with access_denied "
        "(rule=approval_required) before idempotency or admission."
    ),
    "properties": dict(_SUBMIT_PROPERTIES),
    "required": ["workflow", "input", "idempotency_key"],
    "additionalProperties": False,
}

#: Preflight is the zero-side-effect dry run of the same request body, so it
#: accepts the identical field table but does not require a retry key (it creates
#: nothing to retry).
_PREFLIGHT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Preflight/estimate descriptor: the same declared fields as acp_submit, with zero "
        "side effects (no task, no reservation, no metering). idempotency_key is optional "
        "here because nothing is created."
    ),
    "properties": {
        **_SUBMIT_PROPERTIES,
        "idempotency_key": {
            "type": "string",
            "description": (
                "Optional for preflight: nothing is created, so no ledger entry is "
                "claimed. acp_submit requires the key."
            ),
        },
    },
    "required": ["workflow", "input"],
    "additionalProperties": False,
}

#: Artifact reference shared by read/analyze (frozen ``acp2.artifact-ref.v1``
#: identity fields).  Any subset is insufficient: a read/analyze without the full
#: (task_id, attempt, artifact_id, generation, digest) tuple is refused.
_ARTIFACT_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["task_id", "attempt", "artifact_id", "generation", "digest"],
    "properties": {
        "task_id": {"type": "string"},
        "attempt": {"type": "integer", "minimum": 1},
        "artifact_id": {"type": "string"},
        "generation": {"type": "integer", "minimum": 1},
        "digest": {"type": "string"},
        "size_bytes": {"type": "integer", "minimum": 0},
        "media_type": {"type": "string"},
        "relative_path": {"type": "string"},
    },
}

_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Bounded read of an already-prepared artifact. artifact_ref is required; a miss is "
        "resource_not_ready and a read never fetches, prepares or starts computation "
        "max_bytes above the frozen max_read_bytes bound is refused "
        "(input_invalid, rule=max_read_bytes_exceeded) instead of served."
    ),
    "properties": {
        "schema_version": {"type": "integer", "enum": [1], "default": 1},
        "artifact_ref": _ARTIFACT_REF_SCHEMA,
        "max_bytes": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_READ_BYTES,
            "description": (
                f"Requested response bound (<= {MAX_READ_BYTES} bytes). A larger request is "
                "refused rather than silently clamped."
            ),
        },
        "encoding": {"type": "string", "enum": ["identity", "base64"]},
        "extensions": {
            "type": "object",
            "description": "Namespaced extension metadata (never used for execution).",
        },
    },
    "required": ["artifact_ref"],
    "additionalProperties": False,
}

#: Declared analyze properties, keyed by the frozen field name.  The advertised
#: table is built from
#: :data:`~acp_services.entrypoints.application_params.ANALYZE_REQUEST_FIELDS`,
#: so a field that is not in the one application table cannot be advertised or
#: accepted here (M2-02; the registered ``application-api.v1``
#: ``operations.analyze.request`` declares the same names).
_ANALYZE_PROPERTIES: dict[str, Any] = {
    "schema_version": {"type": "integer", "enum": [1], "default": 1},
    "analysis_id": {"type": "string"},
    "input_refs": {
        "type": "array",
        "items": _ARTIFACT_REF_SCHEMA,
        "minItems": 1,
        "description": "Frozen ArtifactRef inputs (full identity tuple).",
    },
    "explicit": {
        "type": "boolean",
        "description": "Must be exactly true; there is no implicit analysis.",
    },
    "parameters": {"type": "object"},
    "plan_digest": {
        "type": "string",
        "description": "Required for vibration.compute (it starts exactly one task).",
    },
    "idempotency_key": {
        "type": "string",
        "description": "Optional retry key for analysis; omitting it is not idempotent.",
    },
    "extensions": {
        "type": "object",
        "description": "Namespaced extension metadata (never used for execution).",
    },
}

_ANALYZE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Explicit analysis request. analysis_id, input_refs and explicit=true are "
        "required: an implicit/heuristic trigger is refused (input_invalid, "
        "rule=explicit_required); an unregistered capability is unsupported_capability "
        "(rule=analysis_capability_not_registered) and a declared-but-not-executable one is "
        "unsupported_capability (rule=analysis_capability_not_executable). Each input_refs "
        "entry must be the full ArtifactRef identity tuple (task_id/attempt/artifact_id/"
        "generation/digest) of an already prepared artifact. vibration.analyze reads "
        "existing evidence and starts no QC; vibration.compute is a compute capability "
        "(submit it as a frequency task) and is never a read-only analysis."
    ),
    "properties": {name: _ANALYZE_PROPERTIES[name] for name in ANALYZE_REQUEST_FIELDS},
    "required": [name for name in ANALYZE_REQUEST_REQUIRED],
    "additionalProperties": False,
}

#: Status is a historical read: extra query fields are preserved (unknown-field
#: policy U2), so its declared table stays permissive.
_STATUS_SCHEMA: dict[str, Any] = {
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

#: Cancel writes one control intent, so it follows the execution-field policy U1:
#: exactly ``reference`` (+ an optional CAS guard).
_CANCEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reference": {
            "description": "Operation/job reference returned by acp_submit (id or {kind,id}).",
            "oneOf": [{"type": "string"}, {"type": "object"}],
        },
        "expected_revision": {"type": "integer", "minimum": 0},
    },
    "required": ["reference"],
    "additionalProperties": False,
}
#: ``acp_prepare_artifact`` is the one call surface whose arguments are the v1
#: prepare request body verbatim (D-IDENT-10, M0-11).  The adapter validates the
#: raw MCP arguments against ``PREPARE_FIELD_POLICY``, so the declared schema is
#: that same authoritative table: ``purpose`` is required, ``artifact_id`` (not
#: ``resource_id``) names the artifact, and ``extensions`` is a submit-only
#: control field.  ``resource_id`` is derived by the Controller route from
#: ``artifact_id`` and is therefore never an argument here.
_PREPARE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Explicit prepare request, identical to POST /api/v1/jobs/{job_id}/prepare. "
        "purpose is required; artifact_id is required when purpose=artifact. The "
        "Controller derives resource_id from artifact_id, never from the caller. A "
        "prepare never starts science and is never implied by a read or a status query."
    ),
    "properties": {
        "schema_version": {"type": "integer", "enum": [1], "default": 1},
        "purpose": {
            "type": "string",
            "enum": ["artifact", "structure-input"],
            "description": (
                "Required. artifact = fetch/commit one artifact revision; "
                "structure-input = local parse+register with no task."
            ),
        },
        "task_ref": {
            "type": "object",
            "description": (
                "Alternate task reference; task_ref.task_id selects the task and "
                "supplies the default attempt."
            ),
        },
        "job_id": {
            "type": "string",
            "description": (
                "Task id to prepare; a mismatch with task_ref.task_id is "
                "revision_conflict."
            ),
        },
        "artifact_id": {
            "type": "string",
            "description": (
                "Required when purpose=artifact. Safe artifact token; the Controller derives "
                "resource_id from it."
            ),
        },
        "attempt": {"type": "integer", "minimum": 1},
        "generation": {"type": "integer", "minimum": 1},
        "expected_revision": {
            "type": "integer",
            "minimum": 0,
            "description": (
                "Declared and normalised for CAS-style callers; the prepare route does not "
                "act on it."
            ),
        },
        "relative_path": {"type": "string"},
        "expected_digest": {
            "type": "string",
            "description": "Trusted sha256:<64 hex> expectation; a mismatch is conflict.",
        },
        "content_digest": {
            "type": "string",
            "description": "Fallback spelling of expected_digest (expected_digest wins).",
        },
        "expected_size_bytes": {"type": "integer", "minimum": 0},
        "schema_ref": {"type": "string"},
        "source_refs": {"type": "array", "items": {"type": "object"}},
        "evidence_report": {"type": "object"},
        "media_type": {"type": "string"},
        "idempotency_key": {
            "type": "string",
            "description": (
                "Client retry key for the prepare operation, forwarded unchanged. The "
                "server derives the versioned key from (principal, project, client key, "
                "epoch). Omitting it is NOT idempotent: a retry may re-prepare."
            ),
        },
    },
    "required": ["purpose"],
    "additionalProperties": False,
}

#: The declared argument tables the adapter enforces itself (required fields +
#: ``additionalProperties: false``).  Status is absent on purpose: a historical
#: read preserves unknown fields (unknown_field_policy U2).
_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    McpTool.PREFLIGHT: _PREFLIGHT_SCHEMA,
    McpTool.SUBMIT: _SUBMIT_SCHEMA,
    McpTool.CANCEL: _CANCEL_SCHEMA,
    McpTool.PREPARE_ARTIFACT: _PREPARE_SCHEMA,
    McpTool.READ_ARTIFACT: _READ_SCHEMA,
    McpTool.ANALYZE: _ANALYZE_SCHEMA,
}


#: The ten research tool schemas (M2-01c).  The frozen M1-01b conventions apply
#: unchanged: ``additionalProperties: false`` and an explicit required set, so a
#: research call can never smuggle an identity, an approver or a task fact.
_PROPOSAL_CREATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_id": {"type": "string", "description": "Project scope (server-verified)."},
        "proposal": {
            "type": "object",
            "description": (
                "C-shaped Proposal body (proposal_id/science/budget/recipe_digest/round)."
            ),
        },
        "plan": {
            "type": "object",
            "description": "Execution plan the approval binds as plan_digest.",
        },
        "session_seed": {"type": "string", "description": "Client-derived research session seed."},
        "budget": {"type": "object", "description": "Launch budget bound by the approval."},
        "expires_at": {"type": "string", "description": "Optional approval expiry (ISO-8601)."},
        "idempotency_key": {"type": "string", "description": "Replay key of this create."},
    },
    "required": ["project_id", "proposal", "plan"],
    "additionalProperties": False,
}

_PROPOSAL_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposal_ref": {"type": "string", "description": "Stored proposal id."},
        "project_id": {"type": "string", "description": "Project scope (server-verified)."},
        "revision": {"type": "integer", "minimum": 1, "description": "Pinned revision."},
    },
    "required": ["proposal_ref"],
    "additionalProperties": False,
}

_PROPOSAL_CAS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposal_ref": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "proposal": {"type": "object"},
        "plan": {"type": "object", "description": "Execution plan (a change opens a new round)."},
        "project_id": {"type": "string"},
        "session_seed": {"type": "string"},
        "budget": {"type": "object"},
        "expires_at": {"type": "string"},
        "idempotency_key": {"type": "string"},
    },
    "required": ["proposal_ref", "expected_revision", "proposal", "plan"],
    "additionalProperties": False,
}

_APPROVAL_REQUEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposal_ref": {"type": "string"},
        "plan_digest": {"type": "string", "description": "Frozen plan digest (sha256:<64 hex>)."},
        "plan": {"type": "object", "description": "Plan definition (digest re-verified)."},
        "proposal": {"type": "object", "description": "Optional proposal body for the digests."},
        "proposal_digest": {"type": "string"},
        "science_digest": {"type": "string"},
        "budget": {"type": "object"},
        "allowed_change_range": {"type": "object"},
        "expires_at": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "comment": {"type": "string", "description": "Recorded audit note; never a decision."},
    },
    "required": ["proposal_ref", "plan_digest"],
    "additionalProperties": False,
}

_APPROVAL_QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_ref": {"type": "string"},
        "proposal_ref": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["draft", "pending", "approved", "rejected", "expired", "revoked"],
        },
        "cursor": {"type": "string", "description": "Opaque bounded-query cursor."},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "project_id": {"type": "string"},
    },
    "required": [],
    "additionalProperties": False,
}

_APPROVAL_DECIDE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_ref": {"type": "string"},
        "decision": {"type": "string", "enum": ["approve", "reject"]},
        "expected_revision": {"type": "integer", "minimum": 1},
        "reason": {"type": "string"},
        "idempotency_key": {"type": "string"},
    },
    "required": ["approval_ref", "decision", "expected_revision"],
    "additionalProperties": False,
}

_APPROVAL_REVOKE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "approval_ref": {"type": "string"},
        "expected_revision": {"type": "integer", "minimum": 1},
        "reason": {"type": "string"},
        "idempotency_key": {"type": "string"},
    },
    "required": ["approval_ref", "expected_revision"],
    "additionalProperties": False,
}

_RESEARCH_WAIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kinds": {
            "type": "array",
            "items": {"type": "string", "enum": ["approval", "saga", "proposal", "record"]},
            "minItems": 1,
        },
        "cursor": {"type": "string", "description": "Opaque cursor (expires after 900 s)."},
        "max_wait_s": {"type": "number", "minimum": 0, "maximum": 60},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "project_id": {"type": "string"},
    },
    "required": ["kinds"],
    "additionalProperties": False,
}

_RESEARCH_SNAPSHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "refs": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "kind:ref references (approval/saga/proposal/record).",
        },
        "project_id": {"type": "string"},
    },
    "required": ["refs"],
    "additionalProperties": False,
}

_RESEARCH_RESUME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposal_ref": {"type": "string"},
        "plan_digest": {"type": "string"},
        "task_ref": {"type": "object", "description": "Expected TaskRef (mismatch is a conflict)."},
        "cursor": {"type": "string"},
        "diagnostic_only": {
            "type": "boolean",
            "description": "Report the position without running the remaining idempotent steps.",
        },
        "project_id": {"type": "string"},
    },
    "required": ["proposal_ref", "plan_digest"],
    "additionalProperties": False,
}

#: Research-only argument table (merged into :data:`_ARGUMENT_SCHEMAS` below).
_RESEARCH_ARGUMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "acp_research_proposal_create_v1": _PROPOSAL_CREATE_SCHEMA,
    "acp_research_proposal_read_v1": _PROPOSAL_READ_SCHEMA,
    "acp_research_proposal_update_cas_v1": _PROPOSAL_CAS_SCHEMA,
    "acp_research_approval_request_v1": _APPROVAL_REQUEST_SCHEMA,
    "acp_research_approval_query_v1": _APPROVAL_QUERY_SCHEMA,
    "acp_research_approval_decide_v1": _APPROVAL_DECIDE_SCHEMA,
    "acp_research_approval_revoke_v1": _APPROVAL_REVOKE_SCHEMA,
    "acp_research_wait_v1": _RESEARCH_WAIT_SCHEMA,
    "acp_research_snapshot_v1": _RESEARCH_SNAPSHOT_SCHEMA,
    "acp_research_resume_v1": _RESEARCH_RESUME_SCHEMA,
}

_ARGUMENT_SCHEMAS.update(_RESEARCH_ARGUMENT_SCHEMAS)
_ARGUMENT_SCHEMAS.update(CONSUMPTION_SCHEMAS)


def _validate_arguments(tool: str, arguments: Mapping[str, Any]) -> None:
    """Reject an undeclared or missing argument *before* any port is touched.

    This is the adapter half of M1-01b: ``acp_submit`` requires
    ``idempotency_key``, every execution-family schema is
    ``additionalProperties: false``, and both failures are typed ``input_invalid``
    results (never a transport error, never a silently forwarded field).  Unknown
    fields are judged first so a misspelt field is reported as the typo it is.

    Raises:
        McpToolError: ``input_invalid`` with ``params.rule``
            ``unknown_field`` or ``missing_required_field``
            (``idempotency_key_required`` for the submit key).
    """
    spec = _ARGUMENT_SCHEMAS.get(tool)
    if spec is None:
        return
    declared = spec.get("properties") or {}
    unknown = sorted(str(name) for name in arguments if str(name) not in declared)
    if unknown:
        raise McpToolError(
            mo.ErrorCode.INPUT_INVALID,
            f"{tool} does not accept the field {unknown[0]!r}",
            params={
                "field": unknown[0],
                "rule": "unknown_field",
                "fields": unknown,
                "tool": tool,
            },
            retryable=False,
        )
    missing = [str(name) for name in spec.get("required") or () if name not in arguments]
    if missing:
        rule = (
            "idempotency_key_required"
            if missing[0] == "idempotency_key"
            else "missing_required_field"
        )
        raise McpToolError(
            mo.ErrorCode.INPUT_INVALID,
            f"{tool} requires the field {missing[0]!r}",
            params={"field": missing[0], "rule": rule, "missing": missing, "tool": tool},
            retryable=False,
        )
    _validate_artifact_refs(tool, arguments)


def _validate_artifact_refs(tool: str, arguments: Mapping[str, Any]) -> None:
    """Refuse an incomplete ArtifactRef *before* any port call (M2-02).

    A bounded read and an explicit analysis are both addressed by the frozen
    ``acp2.artifact-ref.v1`` identity tuple.  The JSON schema documents it; this
    is the adapter half that enforces it locally, so a partial/typo'd reference
    is the same typed ``input_invalid`` here and never reaches a Controller (or
    a custom port) where it would have to be guessed.

    Raises:
        McpToolError: ``input_invalid`` with ``params.rule``
            ``missing_artifact_ref``/``unknown_field``/``digest``/``minimum``.
    """
    if tool == McpTool.READ_ARTIFACT:
        targets: list[tuple[str, Any]] = [("artifact_ref", arguments.get("artifact_ref"))]
    elif tool == McpTool.ANALYZE:
        raw_refs = arguments.get("input_refs")
        if not isinstance(raw_refs, (list, tuple)):
            return
        targets = [(f"input_refs[{index}]", item) for index, item in enumerate(raw_refs)]
    else:
        return
    for field, value in targets:
        try:
            normalize_artifact_ref(value, field=field)
        except ApplicationParamError as exc:
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                exc.message,
                params={"tool": tool, **dict(exc.params)},
                retryable=False,
            ) from None


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
            _PREFLIGHT_SCHEMA,
        ),
        _tool_spec(
            McpTool.SUBMIT,
            "Controlled submit; returns an operation/job reference immediately (long task).",
            _SUBMIT_SCHEMA,
        ),
        _tool_spec(
            McpTool.STATUS,
            "Query the status of an operation/job reference. Holds no connection state.",
            _STATUS_SCHEMA,
        ),
        _tool_spec(
            McpTool.CANCEL,
            "Request cancellation of an operation/job reference.",
            _CANCEL_SCHEMA,
        ),
        _tool_spec(
            McpTool.PREPARE_ARTIFACT,
            "Prepare an artifact for read; idempotent and budget-checked server-side.",
            _PREPARE_SCHEMA,
        ),
        _tool_spec(
            McpTool.READ_ARTIFACT,
            "Read an already-prepared artifact (bounded; no fetch on miss).",
            _READ_SCHEMA,
        ),
        _tool_spec(
            McpTool.ANALYZE,
            "Run an explicit analysis request (requires analysis_id, input_refs, explicit=true).",
            _ANALYZE_SCHEMA,
        ),
        *_research_tool_specs(),
        *(
            _tool_spec(name, "Versioned research consumer operation.", CONSUMPTION_SCHEMAS[name])
            for name in CONSUMPTION_TOOL_NAMES
        ),
    )


def _research_tool_specs() -> tuple[dict[str, Any], ...]:
    """The ten P2 research tool specs (visible only after negotiation)."""
    return (
        _tool_spec(
            "acp_research_proposal_create_v1",
            "Create revision 1 of a research proposal and request its double-digest approval.",
            _PROPOSAL_CREATE_SCHEMA,
        ),
        _tool_spec(
            "acp_research_proposal_read_v1",
            "Read one stored proposal revision (verbatim document + digest).",
            _PROPOSAL_READ_SCHEMA,
        ),
        _tool_spec(
            "acp_research_proposal_update_cas_v1",
            "CAS one proposal revision; a changed plan digest opens a new round.",
            _PROPOSAL_CAS_SCHEMA,
        ),
        _tool_spec(
            "acp_research_approval_request_v1",
            "Request (or replay) the approval of one frozen plan; never decides it.",
            _APPROVAL_REQUEST_SCHEMA,
        ),
        _tool_spec(
            "acp_research_approval_query_v1",
            "Bounded query over the authoritative approval records.",
            _APPROVAL_QUERY_SCHEMA,
        ),
        _tool_spec(
            "acp_research_approval_decide_v1",
            "Approve or reject one approval; the approver is the authenticated principal.",
            _APPROVAL_DECIDE_SCHEMA,
        ),
        _tool_spec(
            "acp_research_approval_revoke_v1",
            "Revoke one approval; a revoked approval can no longer authorize a submit.",
            _APPROVAL_REVOKE_SCHEMA,
        ),
        _tool_spec(
            "acp_research_wait_v1",
            "Bounded wait (max_wait_s<=60, limit<=200) over approval/saga/proposal/record.",
            _RESEARCH_WAIT_SCHEMA,
        ),
        _tool_spec(
            "acp_research_snapshot_v1",
            "Authoritative snapshot of the requested kind:ref references (read-only).",
            _RESEARCH_SNAPSHOT_SCHEMA,
        ),
        _tool_spec(
            "acp_research_resume_v1",
            "Resume the derived research saga without creating a second attempt.",
            _RESEARCH_RESUME_SCHEMA,
        ),
    )


# ---------------------------------------------------------------------------
# Injected ports (the Service use cases the legacy composition root adapts)
# ---------------------------------------------------------------------------


@runtime_checkable
class CapabilityPort(Protocol):
    """Capability discovery (read-only)."""

    def capabilities(self, *, workflow: str | None = None) -> Mapping[str, Any]:
        """Return the current capability descriptor.

        ``workflow`` selects the optional per-workflow contract projection
        (P2).  Omitting it keeps the frozen payload shape unchanged.
        """
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


@runtime_checkable
class ResearchPort(Protocol):
    """The ten P2 research operations (M2-01c), one method per operation.

    The adapter never writes a research store: the implementation is the
    Controller-backed port (:mod:`acp_services.entrypoints.research_port`) whose
    methods are 1:1 with :data:`P2_OPERATION_METHODS`, and the Controller's
    application service owns every write.
    """

    def proposal_create(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Create revision 1 of a proposal and request its approval."""
        ...

    def proposal_read(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Read one stored proposal revision."""
        ...

    def proposal_update_cas(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """CAS one proposal revision."""
        ...

    def approval_request(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Request (or replay) the approval of a frozen plan."""
        ...

    def approval_query(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Bounded query over the approval records."""
        ...

    def approval_decide(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Approve or reject one approval (the credential is the approver)."""
        ...

    def approval_revoke(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Revoke one approval."""
        ...

    def wait(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Bounded wait over the authoritative snapshots."""
        ...

    def snapshot(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Authoritative snapshot of the requested references."""
        ...

    def resume(self, arguments: Mapping[str, Any], *, workspace_id: str) -> Any:
        """Resume the derived saga without creating a second attempt."""
        ...


@dataclass(frozen=True)
class McpBackends:
    """The injected Service use cases the MCP adapter forwards to (one owner).

    ``research`` is optional so a Controller without the P2 application service
    still answers the ten tools with a typed ``unsupported_capability`` refusal
    (``research_service_unavailable``) instead of a transport error.
    """

    capabilities: CapabilityPort
    preflight: PreflightPort
    submit: SubmitPort
    status: StatusPort
    cancel: CancelPort
    artifacts: ArtifactPort
    analysis: AnalysisPort
    research: ResearchPort | None = None
    consumption: ConsumptionPort | None = None


# ---------------------------------------------------------------------------
# Identity mapping (server-enforced)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StdioCredentialBinding:
    """The Controller-resolved credential behind one stdio session (M0-05).

    It is the *server's* answer (``GET /api/identity``) — never a ``_meta``
    claim from the client — and is shaped like
    :class:`~acp_services.workspace.http_identity.VerifiedRequestIdentity` so
    :meth:`CommandIdentity.from_verified_http
    <acp_services.workspace.command_identity.CommandIdentity.from_verified_http>`
    can project it without a second resolver.

    Attributes:
        principal: The verified principal of the credential.
        verified_project: The project the credential is narrowed to.
        key_id: Non-secret reference of the credential.
        key_scope: The credential's permission scope (e.g. ``workspace``).
        actions: The credential's action ceiling.
        mode: The identity mode the Controller reported (``api-key``).
    """

    principal: Principal
    verified_project: str | None = None
    key_id: str | None = None
    key_scope: str | None = None
    actions: tuple[str, ...] = ()
    mode: str = STDIO_MULTIUSER_SCOPE

    @classmethod
    def from_identity_response(cls, payload: Mapping[str, Any]) -> StdioCredentialBinding:
        """Build the binding from the Controller's ``/api/identity`` answer.

        Raises:
            ValueError: the answer carries no principal.
        """
        principal_data = payload.get("principal")
        if not isinstance(principal_data, Mapping):
            raise ValueError("identity response carries no principal")
        verified_project = payload.get("verified_project")
        return cls(
            principal=Principal.from_dict(dict(principal_data)),
            verified_project=(
                None if verified_project is None else str(verified_project).strip() or None
            ),
            key_id=None if payload.get("key_id") is None else str(payload["key_id"]),
            key_scope=None if payload.get("key_scope") is None else str(payload["key_scope"]),
            actions=tuple(str(action) for action in payload.get("actions") or ()),
            mode=str(payload.get("mode") or STDIO_MULTIUSER_SCOPE),
        )


#: Schema version of :class:`StdioCredentialBinding` (diagnostic projection).
STDIO_CREDENTIAL_BINDING_SCHEMA = 1


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
        credential: The full resolved credential binding (M0-05).  When set it is
            the single source for the bound principal/project/actions and for
            :meth:`command_identity`.
        revalidate: Callable invoked before **every** tool call (M0-05).  It must
            return a fresh :class:`StdioCredentialBinding` (or raise
            :class:`McpToolError`).  This is how a revoked credential stops
            reads, writes and control on an already-open connection: the adapter
            holds no cached authority.
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
        credential: StdioCredentialBinding | None = None,
        revalidate: Callable[[], StdioCredentialBinding] | None = None,
    ) -> None:
        self._tier = str(configured_tier)
        self._known_projects = None if known_projects is None else dict(known_projects)
        self._user = user
        self._grants = tuple(grants)
        self._permission_scope = str(permission_scope)
        self._revalidate = revalidate
        self._bound_principal = bound_principal
        self._bound_project = None if bound_project is None else str(bound_project).strip() or None
        self._bound_actions = None if bound_actions is None else tuple(bound_actions)
        self._credential = credential
        if credential is not None:
            self._apply_credential(credential)

    def _apply_credential(self, credential: StdioCredentialBinding) -> None:
        self._credential = credential
        self._bound_principal = credential.principal
        self._bound_project = credential.verified_project
        self._bound_actions = tuple(credential.actions)

    def refresh(self) -> None:
        """Re-resolve the bound credential before one tool call (M0-05).

        A no-op for the ``local-single`` operator entry (there is no credential
        to revalidate).  For a bound multi-user launch the callable is the
        Controller's identity answer: a revoked/expired credential raises, so the
        tool call fails **before** any backend is touched.

        Raises:
            McpToolError: the credential can no longer be verified.
        """
        if self._revalidate is None:
            return
        self._apply_credential(self._revalidate())

    @property
    def bound(self) -> bool:
        return self._bound_principal is not None

    def command_identity(self, context: WorkspaceContext) -> CommandIdentity:
        """Project this request onto the one frozen :class:`CommandIdentity`.

        The identity is built from the **server-side stdio binding** — the
        credential the Controller resolved, never ``_meta``:
        :meth:`CommandIdentity.from_verified_http
        <acp_services.workspace.command_identity.CommandIdentity.from_verified_http>`
        for a bound launch, and the explicit local-operator identity for the
        trusted ``local-single`` transport.
        """
        if self._credential is not None:
            return CommandIdentity.from_verified_http(
                self._credential,
                project_id=context.project_id or self._credential.verified_project,
                entry_class=EntryClass.MCP,
                permission_scope=PermissionScope.STDIO,
            )
        return CommandIdentity.from_local(
            principal_id=context.principal.principal_id,
            project_id=context.project_id,
            entry_class=EntryClass.MCP,
            permission_scope=PermissionScope.STDIO,
            actions=context.available_actions,
            tenant_id=context.principal.tenant_id,
        )

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
# Idempotency (one authority: the Controller's persistent ledger, M0-05)
# ---------------------------------------------------------------------------

#: Prefix of the frozen pre-M0-05 MCP derivation.  It is a *legacy lookup
#: namespace* only: the formula below is byte-frozen so an old client's retry
#: can be recognised, and it is never applied to a client-supplied key.
LEGACY_DERIVED_KEY_PREFIX = "auto_"

#: The rule version of :func:`derive_idempotency_key` (the pre-M0-05 MCP
#: derivation).  Bumping it would make old rows unreachable, so it is frozen.
LEGACY_DERIVED_KEY_VERSION = "auto-v1"

#: The key the caller supplied, forwarded unchanged.
IDEMPOTENCY_KEY_ORIGIN_CLIENT = "client"
#: The frozen legacy ``auto_`` key, used to find a pre-M0-05 row.
IDEMPOTENCY_KEY_ORIGIN_LEGACY = "legacy-derived"
#: No key at all: the request is explicitly non-idempotent.
IDEMPOTENCY_KEY_ORIGIN_NONE = "none"


def derive_idempotency_key(payload: Mapping[str, Any]) -> str:
    """Recompute the frozen pre-M0-05 MCP retry key (legacy lookup only).

    The formula is unchanged from the code that wrote the old rows
    (:data:`LEGACY_DERIVED_KEY_VERSION`), so it can be used to *find* them.
    It never names a new claim for a client that supplied its own key, and it is
    never combined with a permission scope (the old ``op_`` fold is gone: an
    ``op_`` string is an operation reference, not a ledger key).
    """
    material = {key: value for key, value in payload.items() if key != "idempotency_key"}
    blob = json.dumps(material, sort_keys=True, default=str, ensure_ascii=False)
    return LEGACY_DERIVED_KEY_PREFIX + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class IdempotencyResolution:
    """How one tool call names its operation on the Controller ledger.

    Attributes:
        key: The key handed to the submit/prepare port (``""`` = no key, the
            request is non-idempotent).
        origin: :data:`IDEMPOTENCY_KEY_ORIGIN_CLIENT`,
            :data:`IDEMPOTENCY_KEY_ORIGIN_LEGACY` or
            :data:`IDEMPOTENCY_KEY_ORIGIN_NONE`.
        key_scope: The identity scope the key is claimed under (audit only —
            the Controller rebuilds the authoritative scope itself).
        legacy_key: The recomputed ``auto_`` key when ``origin`` is legacy.
    """

    key: str
    origin: str
    key_scope: str = ""
    legacy_key: str | None = None

    @property
    def is_legacy(self) -> bool:
        return self.origin == IDEMPOTENCY_KEY_ORIGIN_LEGACY

    def to_dict(self) -> dict[str, Any]:
        """Non-secret projection published in the tool result."""
        return {
            "origin": self.origin,
            "key": self.key or None,
            "scope_id": self.key_scope or None,
            "legacy_rule_version": LEGACY_DERIVED_KEY_VERSION if self.is_legacy else None,
            "legacy_key": self.legacy_key,
        }


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

#: Fixed dispatch table type: tool name -> bound handler.
_ToolHandler = Callable[[Mapping[str, Any], WorkspaceContext], mo.MachineResult]


class McpServer:
    """A stateless MCP adapter over injected Service use cases.

    The server holds no *task* state: the same instance (or a fresh one sharing
    the same ports/registries) answers any request, and a dropped connection has
    no bearing on a submitted operation.  The one per-session fact it keeps is
    whether **this** session negotiated the research extension (M2-01c) — that is
    what makes ``tools/list`` and the research tools fail-closed before
    negotiation.  ``handle`` is the only entry point; the caller owns stdin/stdout
    framing.

    Args:
        backends: The injected Service use cases.
        identity: Identity mapper (defaults to the local stdio scope).
        budget: Optional in-process cache/test adapter.  ``None`` on the final
            Controller path: the Controller's server-side admission ledger is
            the authority, and a tool hint can never override its grant.
        configured_tier: Deployment tier; request overrides are refused.
        extension: The research extension this server advertises through
            ``acp_capabilities``.  Declared by the composition root (the id is a
            wire literal and belongs there); ``None`` advertises no extension and
            refuses every extension request as ``extension_not_advertised``.

    There is deliberately **no** idempotency cache parameter (M0-05): the
    adapter holds no operation result state, so it can never replay a stale
    answer after a credential revocation or shadow a second principal's key.
    """

    def __init__(
        self,
        *,
        backends: McpBackends,
        identity: IdentityMapper | None = None,
        budget: WorkspaceBudget | None = None,
        configured_tier: str = DeploymentTier.LOCAL_SINGLE,
        extension: ExtensionSpec | None = None,
    ) -> None:
        self._backends = backends
        self._identity = identity or IdentityMapper(configured_tier=configured_tier)
        self._budget = budget
        self._tier = str(configured_tier)
        self._extension = extension
        # M2-01c: the ten research tools are registered but *not visible* until
        # this session negotiated the extension (``tools/list`` is the model-visible
        # surface; an unnegotiated call is refused in-band).
        self._research_negotiated = False
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
        for tool_name in RESEARCH_TOOL_NAMES:
            self._tool_dispatch[tool_name] = self._research_handler(tool_name)

        for tool_name in CONSUMPTION_TOOL_NAMES:
            self._tool_dispatch[tool_name] = self._consumption_handler(tool_name)

    def _consumption_handler(self, tool_name: str) -> _ToolHandler:
        def handle(arguments: Mapping[str, Any], context: WorkspaceContext) -> mo.MachineResult:
            if self._backends.consumption is None:
                raise McpToolError(
                    mo.ErrorCode.UNSUPPORTED_CAPABILITY, "consumer service unavailable"
                )
            result = self._backends.consumption.call(tool_name, arguments)
            return mo.MachineResult(
                operation=tool_name,
                status="ok",
                data=dict(result),
                operation_identity=context.workspace_id,
            )

        return handle

    def _research_handler(self, tool_name: str) -> _ToolHandler:
        """Bind one dispatch entry to a research tool name (no client input)."""

        def handler(arguments: Mapping[str, Any], context: WorkspaceContext) -> mo.MachineResult:
            return self._tool_research(tool_name, arguments, context)

        return handler

    # -- research negotiation state (M2-01c) --------------------------------

    @property
    def research_negotiated(self) -> bool:
        """Whether this session negotiated the research extension."""
        return self._research_negotiated

    def visible_tool_names(self) -> tuple[str, ...]:
        """The tools ``tools/list`` advertises right now (P1 + negotiated P2)."""
        names = sorted(TOOL_ALLOWLIST)
        if self._research_negotiated:
            names.extend(sorted(RESEARCH_TOOL_SET))
            if self._backends.consumption is not None:
                names.extend(CONSUMPTION_TOOL_NAMES)
        return tuple(names)

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
        visible = set(self.visible_tool_names())
        return {"tools": [spec for spec in _tool_specs() if spec["name"] in visible]}

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
                data={"code": "unknown_tool", "allowlist": sorted(REGISTERED_TOOL_NAMES)},
            )
        if (
            name in (RESEARCH_TOOL_SET | frozenset(CONSUMPTION_TOOL_NAMES))
            and not self._research_negotiated
        ):
            # M2-01c fail-closed: a research tool exists only for a session that
            # negotiated the extension.  Never a transport error, never a silent
            # downgrade to a P1 tool.
            error = mo.error_object(
                mo.ErrorCode.UNSUPPORTED_CAPABILITY,
                "the research extension is not negotiated in this session",
                params={
                    "rule": "extension_not_negotiated",
                    "tool": name,
                    "negotiation_tool": McpTool.CAPABILITIES,
                    "negotiation_request_field": "research_extension",
                },
                retryable=False,
            )
            return self._result_payload(
                mo.MachineResult(
                    operation=name,
                    status="error",
                    exit_code=error.exit_code,
                    error=error,
                )
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
            # M0-05: every tool call re-resolves the server-side credential.  A
            # revoked key therefore refuses reads, writes and control on an
            # already-open connection, with no cached result to leak.
            self._identity.refresh()
            context = self._identity.map(meta)
            self._authorize(name, context)
            # M1-01b: the declared argument table is enforced here, before any
            # port sees the request (an undeclared field is never forwarded).
            _validate_arguments(name, arguments)
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
        required = (
            CONSUMPTION_ACTIONS.get(name)
            or TOOL_REQUIRED_ACTION.get(name)
            or RESEARCH_TOOL_REQUIRED_ACTION.get(name, AccessAction.READ)
        )
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
        workflow = arguments.get("workflow")
        if workflow is not None and not isinstance(workflow, str):
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "workflow must be a string",
                params={"field": "workflow"},
            )
        if workflow is None:
            data = dict(self._backends.capabilities.capabilities())
        else:
            data = dict(self._backends.capabilities.capabilities(workflow=workflow))
        data.setdefault("protocol_version", MCP_PROTOCOL_VERSION)
        # M1-01b/M2-02: the frozen three-state analysis-capability projection.  A
        # descriptor is never "available": registered/executable/unavailable are
        # the only states, and analyze refuses anything that is not executable.
        # The Controller's composition root injects the application registry, so a
        # payload that already carries the projection is forwarded verbatim; the
        # fallback is an explicitly empty table, never the process-global default
        # registry (that module-level object is not a production authority).
        data.setdefault(
            "analysis_capabilities", list(capability_projection(AnalysisCapabilityRegistry()))
        )

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
        if handshake.negotiated:
            # M2-01c: a negotiated session is the only one that may see/call the
            # ten research tools.  Negotiation is a per-session fact, so it flips
            # here and never in a shared module-level default.
            self._research_negotiated = True
        data["tools"] = list(self.visible_tool_names())
        data["research_consumer_tools"] = {
            "version": CONSUMPTION_VERSION,
            "available": self._backends.consumption is not None,
            "tools": list(CONSUMPTION_TOOL_NAMES)
            if self._research_negotiated and self._backends.consumption is not None
            else [],
        }
        data["research_tools"] = {
            "registered": sorted(RESEARCH_TOOL_SET),
            "visible": self._research_negotiated,
            "negotiated": bool(handshake.negotiated),
            "promise_covers_registered": RESEARCH_TOOL_SET <= REGISTERED_TOOL_NAMES,
        }

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

    def _tool_research(
        self, tool_name: str, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> mo.MachineResult:
        """Forward one P2 research tool to the research application service.

        The adapter holds no research store and no business rule: it resolves the
        operation from :data:`RESEARCH_TOOL_OPERATIONS` and calls the one port
        method named after that operation, so a tool and its application use case
        cannot drift.  A missing port is a typed refusal, never a transport error.
        """
        port = self._backends.research
        if port is None:
            raise McpToolError(
                mo.ErrorCode.UNSUPPORTED_CAPABILITY,
                "no research application service is wired for this Controller",
                params={"rule": "research_service_unavailable", "tool": tool_name},
                retryable=False,
            )
        operation = RESEARCH_TOOL_OPERATIONS[tool_name]
        # A *literal* table of the port's methods (never resolved from client
        # input, never a dynamic dispatch).
        operations = {
            "proposal_create": port.proposal_create,
            "proposal_read": port.proposal_read,
            "proposal_update_cas": port.proposal_update_cas,
            "approval_request": port.approval_request,
            "approval_query": port.approval_query,
            "approval_decide": port.approval_decide,
            "approval_revoke": port.approval_revoke,
            "wait": port.wait,
            "snapshot": port.snapshot,
            "resume": port.resume,
        }
        result = operations[operation](dict(arguments), workspace_id=context.workspace_id)
        payload = dict(result) if isinstance(result, Mapping) else {"result": result}
        payload.setdefault("tool", tool_name)
        payload.setdefault("operation", operation)
        return mo.MachineResult(
            operation=tool_name,
            status="ok",
            data=payload,
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
        """Forward one submit with the client's raw idempotency key (M0-05).

        There is no adapter-side cache and no key rewriting: the Controller's
        persistent ledger claims ``(verified identity, raw key)`` and answers
        ``created``/``replay``/``conflict``.  The full Controller response is
        forwarded, so a replay carries the same complete TaskRef as the first
        call.
        """
        raw_key = arguments.get("idempotency_key")
        if not isinstance(raw_key, str) or not raw_key.strip():
            # M1-01b: a key-less submit is refused, never silently non-idempotent.
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                "acp_submit requires a non-empty idempotency_key",
                params={
                    "field": "idempotency_key",
                    "rule": "idempotency_key_required",
                    "why": (
                        "a retry without a key may create a second task; use a high-entropy "
                        "UUID or a namespaced key"
                    ),
                },
                retryable=False,
            )
        resolution = self._resolve_idempotency(arguments, context)
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
                arguments, idempotency_key=resolution.key, workspace_id=context.workspace_id
            )
        except McpToolError:
            if self._budget is not None:
                self._budget.release(context.workspace_id, cores=cores)
            raise
        reference = _reference_from(record, kind="job")
        # Full response passthrough: every field the Controller returned is part
        # of the replay contract (task_ref/attempt/state/identity/admission...).
        payload: dict[str, Any] = dict(record) if isinstance(record, Mapping) else {}
        payload["reference"] = payload.get("reference") or reference
        if not isinstance(payload.get("task_ref"), Mapping):
            task_id = str(payload["reference"].get("id") or "")
            payload["task_ref"] = {"task_id": task_id, "job_id": task_id}
        payload["idempotency_key"] = resolution.key or None
        payload["idempotency_key_origin"] = resolution.origin
        payload["idempotency"] = resolution.to_dict()
        payload.setdefault("query_tool", McpTool.STATUS)
        payload.setdefault("cancel_tool", McpTool.CANCEL)
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
        """Forward one prepare with the raw key; the Controller owns the dedup.

        M0-05: the prepare key is versioned server-side by
        :func:`acp_services.data.operations.idempotency_key_for` (v2 =
        ``(principal, project, client_key, epoch)``).  The adapter does not fold
        the key, does not cache the operation reference, and forwards the whole
        response envelope.
        """
        resolution = self._resolve_idempotency(arguments, context)
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
                arguments, idempotency_key=resolution.key, workspace_id=context.workspace_id
            )
        except McpToolError:
            if self._budget is not None:
                self._budget.release(context.workspace_id)
            raise
        reference = _reference_from(record, kind="operation")
        payload: dict[str, Any] = dict(record) if isinstance(record, Mapping) else {}
        payload["reference"] = payload.get("reference") or reference
        payload["idempotency_key"] = resolution.key or None
        payload["idempotency_key_origin"] = resolution.origin
        payload["idempotency"] = resolution.to_dict()
        payload.setdefault("query_tool", McpTool.STATUS)
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
        data = dict(record) if isinstance(record, Mapping) else {"result": record}
        self._verify_artifact_body(data)
        return mo.MachineResult(
            operation=McpTool.READ_ARTIFACT,
            status="ok",
            data=dict(data),
            operation_identity=context.workspace_id,
            passthrough_body=data.get("encoding") == "base64",
        )

    @staticmethod
    def _verify_artifact_body(data: Mapping[str, Any]) -> None:
        """Refuse reads whose body was altered after the digest was computed.

        MachineResult redacts on serialization, so a path inside the body would
        otherwise be returned under the original digest.  A truncated slice is
        exempt: its digest applies to the whole artifact, not the slice.
        """
        body = data.get("body")
        if not isinstance(body, str):
            return
        encoding = data.get("encoding") or "identity"
        import base64
        import binascii
        import hashlib

        from acp_services.entrypoints.machine_output import redact

        if encoding == "base64":
            try:
                raw = base64.b64decode(body, validate=True)
            except (binascii.Error, ValueError):
                raise McpToolError(
                    mo.ErrorCode.INPUT_INVALID,
                    "artifact body is not valid base64",
                    params={"field": "body", "rule": "artifact_encoding_invalid"},
                    retryable=False,
                ) from None
        elif encoding == "identity":
            raw = body.encode("utf-8")
        else:
            raise McpToolError(
                mo.ErrorCode.INPUT_INVALID,
                f"unknown artifact body encoding {encoding!r}",
                params={"field": "encoding", "rule": "artifact_encoding_invalid"},
                retryable=False,
            )
        # One content policy for both encodings: a decoded path/secret is
        # refused, never smuggled through base64.  Undecodable bytes decode with
        # surrogate escapes so ASCII path fragments are still visible.
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Binary bodies cannot be content-checked this round; neither a
            # slice nor the whole blob is returned (residual: binary allowlist).
            raise McpToolError(
                mo.ErrorCode.UNSUPPORTED_CAPABILITY,
                "non-UTF-8 artifact body cannot be verified",
                params={"field": "body", "rule": "artifact_body_unverifiable"},
                retryable=False,
            ) from None
        if redact(decoded) != decoded:
            raise McpToolError(
                mo.ErrorCode.UNSUPPORTED_CAPABILITY,
                "artifact body contains a redacted pattern; the bytes cannot be "
                "returned under the frozen digest",
                params={"field": "body", "rule": "artifact_body_redacted"},
            )
        if data.get("truncated"):
            data.setdefault("digest_scope", "artifact")
            return
        digest = str(data.get("digest") or "")
        expected = digest.split(":", 1)[1] if digest.startswith("sha256:") else digest
        if not expected:
            return
        if hashlib.sha256(raw).hexdigest() != expected:
            raise McpToolError(
                mo.ErrorCode.UNSUPPORTED_CAPABILITY,
                "artifact body does not match the frozen digest",
                params={"field": "body", "rule": "artifact_digest_mismatch"},
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

    def _resolve_idempotency(
        self, arguments: Mapping[str, Any], context: WorkspaceContext
    ) -> IdempotencyResolution:
        """Resolve the ledger key of one write/control call (M0-05).

        Order of judgement (never collapsed):

        1. a client-supplied ``idempotency_key`` is forwarded **unchanged** —
           the Controller's ledger looks it up under the *verified* identity;
        2. otherwise the frozen legacy ``auto_`` derivation is recomputed so a
           pre-M0-05 client's retry finds its row.  Only the explicit
           ``local-single`` operator binding may claim that namespace (it is the
           only writer of it); an authenticated binding stays key-less.  M1-01b
           made the key mandatory for ``acp_submit`` (a key-less submit is a typed
           refusal), so this lookup now covers the remaining optional-key surface
           (prepare) and historical rows only;
        3. the derived key is never folded with a permission scope: an
           ``op_``-shaped string is an operation reference, never a ledger key.
        """
        identity = self._identity.command_identity(context)
        raw = arguments.get("idempotency_key")
        scope_id = identity.idempotency_scope()
        if isinstance(raw, str) and raw.strip():
            return IdempotencyResolution(
                key=raw.strip(),
                origin=IDEMPOTENCY_KEY_ORIGIN_CLIENT,
                key_scope=scope_id,
            )
        if not identity.is_operator:
            return IdempotencyResolution(
                key="",
                origin=IDEMPOTENCY_KEY_ORIGIN_NONE,
                key_scope=scope_id,
            )
        legacy_key = derive_idempotency_key(arguments)
        return IdempotencyResolution(
            key=legacy_key,
            origin=IDEMPOTENCY_KEY_ORIGIN_LEGACY,
            key_scope=scope_id,
            legacy_key=legacy_key,
        )


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
