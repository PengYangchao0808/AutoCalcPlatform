"""ExecutionEnvelope / ExecutionReceipt — frozen execution identity contracts.

B06-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 30).  These are
the *frozen* wire contracts that a Services worker consumes to execute exactly
one attempt of a job, and that it writes back as an observation/completion
receipt.  The field whitelist itself is frozen in
``docs/reference/next-change/ACP_Services_First_Slice_Contracts.md`` §5; this
module is the executable form of that table.

Dependency direction (allowlist ``tests/baseline/services-evidence/public_allowlist.md``)
----------------------------------------------------------------------------------------
``src/acp_services/**`` must never import ``acp``.  This module therefore imports
**stdlib only** and re-derives every frozen semantic from its documented source
instead of importing it.  Where the legacy code is the authority for a semantic,
the ``file:line`` is cited in the relevant docstring:

* ``submission_id`` — deterministic ``sub_`` + ``sha256(f"{job_id}:{attempt}")[:16]``,
  persisted before ``bsub`` (:mod:`acp.scheduler.remote.submission`,
  ``src/acp/scheduler/remote/submission.py:56-64``).  Import is forbidden here, so
  :func:`expected_submission_id` re-implements the derivation purely (same
  algorithm, byte-for-byte) for callers that want to assert it.
* ``release`` — content-addressed release identity: ``release_id`` =
  ``sha256(canonical JSON over ``files``)[:16]``; the manifest is
  :class:`acp.scheduler.remote.release.ReleaseManifest`
  (``src/acp/scheduler/remote/release.py:109-155``, ``:287-309``).
* frozen plan — two-layer science identity: ``plan_identity`` (whole plan) and
  per-step ``step_identities`` (prefix-cumulative), both
  ``"v2:" + sha256(canonical JSON)[:32]``
  (``src/acp/calculations/identity.py:366-392``).
* resource budget — field names mirror the real resolver
  :func:`acp.scheduler.resources.resolve_job_resources`
  (``src/acp/scheduler/resources.py:14-41``): ``nproc`` (int) + ``mem``
  (normalized suffix string); ``cores``/``mem_mb``/``memory_gb`` are accepted as
  documented migration aliases.
* execution protocols — the deployment/execution-mode vocabulary frozen in the
  contract doc §1.5 (``local`` / ``lsf`` / ``slurm`` / ``podman``).

Two hard invariants
-------------------
1. **TaskContext is never serialized.**  ``TaskContext``
   (``src/cccp/calculation/context.py:41``) is the in-process runtime companion
   of a request (config / workdir / progress sink / cancel callback) and has no
   wire form.  Any attempt to put runtime state (a callable, a lock, a generic
   dataclass, a ``TaskContext`` value, or the ``task_context`` key) into an
   envelope/receipt raises :class:`TaskContextSerializationError`.
2. **Protocol selection is frozen per attempt.**  A given ``attempt`` records the
   protocol + release snapshot it was dispatched with; re-selecting a protocol on
   the *same* attempt (e.g. resume) is rejected by :meth:`ExecutionEnvelope.select_protocol`
   / :func:`assert_resume_does_not_reselect`.  Only a *new* attempt
   (:meth:`ExecutionEnvelope.advance_attempt`) may choose anew.

Version compatibility ("v1 implicit" / "v2 explicit")
-----------------------------------------------------
The frozen contract doc (§5) and the tracked samples
(``tests/baseline/services-evidence/contract_samples/execution_*.json``) use the
wire field ``envelope_version``: ``1`` = the explicit envelope (this module's
current form), ``0`` = the pre-envelope *implicit* legacy payload.  The plan's
prose names the two generations "v1 = 现状隐式" / "v2 = envelope 显式"; the mapping
is therefore:

===============  =======================  ===========================
plan prose       wire ``envelope_version`` meaning
===============  =======================  ===========================
"v1 implicit"    ``0`` (or absent)        legacy payload → migrated
"v2 explicit"    ``1``                    current explicit envelope
===============  =======================  ===========================

:func:`migrate_legacy_envelope` performs the documented migration (camelCase
alias folding + version bump + default filling).  ``Receipt`` uses
``receipt_version`` with the same 0-legacy / 1-current convention.

Author: ACP Services (B lane, todo 30).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Final

__all__ = [
    "ATTEMPT_PROTOCOL_FROZEN",
    "CURRENT_ENVELOPE_VERSION",
    "CURRENT_RECEIPT_VERSION",
    "DEFAULT_ENTRYPOINT",
    "ENVELOPE_CONTRACT_VERSION",
    "ENTRYPOINT_WHITELIST",
    "FORBIDDEN_ENVELOPE_FIELDS",
    "FORBIDDEN_RECEIPT_FIELDS",
    "LEGACY_IMPLICIT_ENVELOPE_VERSION",
    "RECEIPT_STATES",
    "RESOURCE_ALIASES",
    "SUBMISSION_ID_PREFIX",
    "SUPPORTED_CAPABILITIES",
    "SUPPORTED_EXECUTION_PROTOCOLS",
    "AttemptProtocolError",
    "EnvelopeError",
    "EnvelopeSerializationError",
    "EnvelopeVersionError",
    "ExecutionEnvelope",
    "ExecutionError",
    "ExecutionReceipt",
    "ResourceBudget",
    "ScientificClosure",
    "StorageBinding",
    "TaskContextSerializationError",
    "UnknownFieldError",
    "UnsupportedCapabilityError",
    "assert_capabilities_supported",
    "assert_resume_does_not_reselect",
    "check_capabilities",
    "ensure_no_runtime_state",
    "expected_submission_id",
    "migrate_legacy_envelope",
    "migrate_legacy_receipt",
]

# ── versions ────────────────────────────────────────────────────────────────

#: Contract-doc generation this module implements (contract doc §5, `1`).
ENVELOPE_CONTRACT_VERSION: Final = 1
#: Explicit wire version of the current envelope (frozen samples: normal=1).
CURRENT_ENVELOPE_VERSION: Final = 1
#: Pre-envelope implicit legacy payload (frozen samples: old-version=0).
LEGACY_IMPLICIT_ENVELOPE_VERSION: Final = 0
#: Explicit wire version of the current receipt.
CURRENT_RECEIPT_VERSION: Final = 1
#: Legacy receipt version (implicit).
LEGACY_IMPLICIT_RECEIPT_VERSION: Final = 0

SUBMISSION_ID_PREFIX: Final = "sub_"

# ── capability / protocol vocabulary ────────────────────────────────────────

#: Execution protocols (contract doc §1.5 deployment/execution vocabulary).
SUPPORTED_EXECUTION_PROTOCOLS: Final[frozenset[str]] = frozenset(
    {"local", "lsf", "slurm", "podman"}
)

#: Worker entrypoints allowed to consume a frozen envelope.
DEFAULT_ENTRYPOINT: Final = "worker.execute"
ENTRYPOINT_WHITELIST: Final[frozenset[str]] = frozenset(
    {"worker.execute", "worker.bootstrap", "worker.resume", "worker.reconcile"}
)

#: Capabilities a worker may be asked to execute.  Grounded in the cccp
#: task-kind vocabulary (``src/cccp/calculation/requests.py:54`` — ``TaskKind``).
SUPPORTED_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "singlepoint",
        "optimize",
        "frequency",
        "scan",
        "irc",
        "casscf",
        "thermochemistry",
        "conformer_search",
        "md_sampling",
        "clustering",
        "censo_refine",
        "nmr_shielding",
        "xtb_path_search",
        "orca_gradient",
        "xtb_constrained_opt",
    }
)

#: Receipt terminal-state vocabulary (contract doc §5.2).
RECEIPT_STATES: Final[frozenset[str]] = frozenset({"completed", "failed", "cancelled", "blocked"})

#: Keys that carry in-process runtime state and are therefore forbidden on the
#: wire (TaskContext and its companions; ``src/cccp/calculation/context.py:41``).
FORBIDDEN_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "task_context",
        "context",
        "task_ctx",
        "progress",
        "progress_callback",
        "cancelled",
        "cancel_callback",
        "config",
        "workdir",
        "input_base",
        "backend",
        "capability_extras",
    }
)
FORBIDDEN_RECEIPT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "task_context",
        "context",
        "task_ctx",
        "progress",
        "progress_callback",
        "cancelled",
        "cancel_callback",
        "config",
        "workdir",
        "backend",
    }
)

#: Resource-budget aliases accepted on decode (legacy/sample names → canonical).
#: Canonical names mirror ``acp.scheduler.resources.resolve_job_resources``.
RESOURCE_ALIASES: Final[dict[str, str]] = {
    "cores": "nproc",
    "ncores": "nproc",
    "memory_gb": "mem_mb",
}

#: Attempt result marker: the protocol snapshot is immutable for an attempt.
ATTEMPT_PROTOCOL_FROZEN: Final = True

_ENVELOPE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "envelope_version",
        "job_id",
        "attempt",
        "submission_id",
        "release_id",
        "release_digest",
        "owner",
        "fencing_token",
        "plan_digest",
        "plan_identity",
        "step_identities",
        "protocol",
        "entrypoint",
        "required_capabilities",
        "data_bindings",
        "resource_budget",
        "scientific_closure",
    }
)
_RECEIPT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "receipt_version",
        "job_id",
        "attempt",
        "submission_id",
        "state",
        "result_digest",
        "errors",
        "written_at",
        "release_id",
        "release_digest",
        "owner",
        "fencing_token",
        "plan_identity",
        "step_identities",
        "data_bindings",
        "evidence_digests",
    }
)

# Legacy camelCase aliases folded by the migration helpers.
_LEGACY_ENVELOPE_ALIASES: Final[dict[str, str]] = {
    "envelopeVersion": "envelope_version",
    "schema_version": "envelope_version",
    "submissionId": "submission_id",
    "jobId": "job_id",
    "releaseId": "release_id",
    "releaseDigest": "release_digest",
    "fencingToken": "fencing_token",
    "planDigest": "plan_digest",
    "planIdentity": "plan_identity",
    "stepIdentities": "step_identities",
    "dataBindings": "data_bindings",
    "resourceBudget": "resource_budget",
    "scientificClosure": "scientific_closure",
}
_LEGACY_RECEIPT_ALIASES: Final[dict[str, str]] = {
    "receiptVersion": "receipt_version",
    "schema_version": "receipt_version",
    "submissionId": "submission_id",
    "jobId": "job_id",
    "resultDigest": "result_digest",
    "writtenAt": "written_at",
    "releaseId": "release_id",
    "releaseDigest": "release_digest",
    "fencingToken": "fencing_token",
    "planIdentity": "plan_identity",
    "stepIdentities": "step_identities",
    "dataBindings": "data_bindings",
    "evidenceDigests": "evidence_digests",
}


# ── errors ──────────────────────────────────────────────────────────────────


class EnvelopeError(Exception):
    """Base class for all envelope/receipt contract violations."""


class EnvelopeSerializationError(EnvelopeError):
    """An envelope/receipt payload cannot be (de)serialized per the contract."""


class TaskContextSerializationError(EnvelopeSerializationError):
    """Runtime state (TaskContext / callable / lock) was offered to the wire.

    TaskContext (``src/cccp/calculation/context.py:41``) is never serialized.
    """


class UnknownFieldError(EnvelopeSerializationError):
    """An unknown field was present (unknown fields are rejected, not ignored)."""


class EnvelopeVersionError(EnvelopeSerializationError):
    """An unsupported/future schema version was requested."""


class UnsupportedCapabilityError(EnvelopeError):
    """An envelope requires a protocol/capability the worker does not support."""

    def __init__(self, message: str, *, unsupported: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.unsupported: tuple[str, ...] = tuple(unsupported)


class AttemptProtocolError(EnvelopeError):
    """Attempt protocol/release snapshot was re-selected or advanced illegally."""


# ── pure helpers ────────────────────────────────────────────────────────────


def expected_submission_id(job_id: str, attempt: int | None) -> str:
    """Return ``sub_`` + sha256(``"{job_id}:{attempt}"``)[:16].

    Byte-for-byte the derivation in the legacy authority
    (``src/acp/scheduler/remote/submission.py:56-64``) without importing it.
    """
    material = f"{job_id}:{int(attempt or 1)}"
    return SUBMISSION_ID_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _looks_like_task_context(value: object) -> bool:
    """Duck-typed TaskContext detector (no ``cccp`` import needed)."""
    if value is None:
        return False
    for attr in ("emit_progress", "is_cancelled", "progress_errors"):
        if hasattr(value, attr):
            return True
    return False


def ensure_no_runtime_state(value: object, *, path: str = "value") -> None:
    """Raise :class:`TaskContextSerializationError` if *value* holds runtime state.

    Runtime state is anything that has no JSON wire form: callables, locks, a
    ``TaskContext`` instance, or any object that is not a JSON scalar, a
    ``Mapping``/``Sequence``, or one of the frozen carrier dataclasses defined
    here.  This is the single guard that makes invariant (1) executable.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return
    if isinstance(value, (StorageBinding, ResourceBudget, ScientificClosure, ExecutionError)):
        return
    if _looks_like_task_context(value):
        raise TaskContextSerializationError(
            f"{path}: TaskContext/runtime state is never serialized"
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TaskContextSerializationError(f"{path}: mapping key is not a string")
            ensure_no_runtime_state(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            ensure_no_runtime_state(item, path=f"{path}[{index}]")
        return
    raise TaskContextSerializationError(
        f"{path}: cannot serialize {type(value).__name__}; "
        "TaskContext/runtime state is never serialized"
    )


def _as_int(value: object, *, name: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise EnvelopeSerializationError(f"{name} must be an int, got {type(value).__name__}")
    return int(value)


def _as_str(value: object, *, name: str, default: str = "") -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise EnvelopeSerializationError(f"{name} must be a str, got {type(value).__name__}")
    return value


def _as_str_tuple(value: object, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise EnvelopeSerializationError(f"{name} must be a sequence of str, not a bare str")
    if not isinstance(value, (list, tuple)):
        raise EnvelopeSerializationError(f"{name} must be a list/tuple")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise EnvelopeSerializationError(f"{name} entries must be str")
        out.append(item)
    return tuple(out)


def _as_str_map(value: object, *, name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EnvelopeSerializationError(f"{name} must be a mapping")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise EnvelopeSerializationError(f"{name} must map str -> str")
        out[key] = item
    return out


def _reject_forbidden(payload: Mapping[str, Any], forbidden: frozenset[str], *, what: str) -> None:
    present = [key for key in forbidden if key in payload]
    if present:
        joined = ", ".join(sorted(present))
        raise TaskContextSerializationError(
            f"{what}: runtime-state field(s) {joined!r} are never serialized "
            "(TaskContext is in-process only)"
        )


def _reject_unknown(payload: Mapping[str, Any], known: frozenset[str], *, what: str) -> None:
    unknown = sorted(set(payload) - known)
    if unknown:
        raise UnknownFieldError(f"{what}: unknown field(s) {unknown!r}")


def _fold_aliases(payload: Mapping[str, Any], aliases: Mapping[str, str]) -> dict[str, Any]:
    folded: dict[str, Any] = {}
    for key, value in payload.items():
        folded[aliases.get(key, key)] = value
    return folded


def migrate_legacy_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicit-version envelope dict migrated from a legacy payload.

    Legacy (``envelope_version`` absent or ``0``) bodies are folded: camelCase /
    ``schema_version`` aliases are renamed, the version is bumped to
    :data:`CURRENT_ENVELOPE_VERSION`, and absent additive fields are left to
    their defaults.  Never mutates *payload*.
    """
    folded = _fold_aliases(dict(payload), _LEGACY_ENVELOPE_ALIASES)
    folded["envelope_version"] = CURRENT_ENVELOPE_VERSION
    return folded


def migrate_legacy_receipt(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicit-version receipt dict migrated from a legacy payload."""
    folded = _fold_aliases(dict(payload), _LEGACY_RECEIPT_ALIASES)
    folded["receipt_version"] = CURRENT_RECEIPT_VERSION
    return folded


def check_capabilities(
    envelope: ExecutionEnvelope,
    *,
    supported: frozenset[str] = SUPPORTED_CAPABILITIES,
) -> tuple[str, ...]:
    """Return the envelope's required capabilities not in *supported* (empty = ok)."""
    return tuple(sorted(set(envelope.required_capabilities) - set(supported)))


def assert_capabilities_supported(
    envelope: ExecutionEnvelope,
    *,
    supported: frozenset[str] = SUPPORTED_CAPABILITIES,
) -> None:
    """Raise :class:`UnsupportedCapabilityError` for unsupported capabilities.

    This is the explicit, non-silent failure semantics required by the plan:
    an envelope that names a capability the worker cannot execute is rejected
    before any dispatch, never degraded or ignored.
    """
    missing = check_capabilities(envelope, supported=supported)
    if missing:
        raise UnsupportedCapabilityError(
            f"unsupported required capabilities: {list(missing)}",
            unsupported=missing,
        )


# ── carrier dataclasses ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class StorageBinding:
    """Stable storage identity + artifact digests frozen for one execution.

    Grounded in the architecture plan §37: a binding fixes the tenant/project
    identity, the storage endpoint, the controlled root and the full relative
    path plus a mapping version; display-name changes never re-allocate it.
    ``artifact_digests`` maps a relative artifact path to its sha256 hex digest
    (the "工件摘要" of B06-1).  The binding is owned by Data and only frozen /
    consumed by Runtime.
    """

    binding_id: str
    tenant_id: str = ""
    project_id: str = ""
    endpoint: str = ""
    root: str = ""
    relative_path: str = ""
    mapping_version: int = 1
    artifact_digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "binding_id", _as_str(self.binding_id, name="binding_id"))
        object.__setattr__(self, "mapping_version", int(self.mapping_version))
        object.__setattr__(
            self, "artifact_digests", _as_str_map(self.artifact_digests, name="artifact_digests")
        )

    def to_dict(self) -> dict[str, Any]:
        ensure_no_runtime_state(self.artifact_digests, path="artifact_digests")
        return {
            "binding_id": self.binding_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "endpoint": self.endpoint,
            "root": self.root,
            "relative_path": self.relative_path,
            "mapping_version": self.mapping_version,
            "artifact_digests": dict(self.artifact_digests),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> StorageBinding:
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("StorageBinding payload must be a mapping")
        return cls(
            binding_id=_as_str(payload.get("binding_id"), name="binding_id"),
            tenant_id=_as_str(payload.get("tenant_id"), name="tenant_id"),
            project_id=_as_str(payload.get("project_id"), name="project_id"),
            endpoint=_as_str(payload.get("endpoint"), name="endpoint"),
            root=_as_str(payload.get("root"), name="root"),
            relative_path=_as_str(payload.get("relative_path"), name="relative_path"),
            mapping_version=_as_int(
                payload.get("mapping_version"), name="mapping_version", default=1
            ),
            artifact_digests=_as_str_map(payload.get("artifact_digests"), name="artifact_digests"),
        )


@dataclass(frozen=True)
class ResourceBudget:
    """Resource budget frozen for one execution.

    Canonical field names mirror ``resolve_job_resources``
    (``src/acp/scheduler/resources.py:14-41``): ``nproc`` (int cores) and
    ``mem`` (normalized suffix string).  Legacy/sample aliases
    (``cores``/``ncores``/``memory_gb``) are folded on decode via
    :data:`RESOURCE_ALIASES`; ``mem_mb`` carries an integer-megabyte form.
    ``queue`` and ``walltime_s`` cover the LSF queue / duration budget;
    ``partial`` marks a deliberately partial budget (contract doc §5.4).
    """

    nproc: int | None = None
    mem: str | None = None
    mem_mb: int | None = None
    queue: str | None = None
    walltime_s: int | None = None
    partial: bool = False
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.nproc is not None:
            object.__setattr__(self, "nproc", int(self.nproc))
        if self.mem_mb is not None:
            object.__setattr__(self, "mem_mb", int(self.mem_mb))
        if self.walltime_s is not None:
            object.__setattr__(self, "walltime_s", int(self.walltime_s))
        object.__setattr__(self, "partial", bool(self.partial))
        object.__setattr__(self, "extra", dict(self.extra))

    def to_dict(self) -> dict[str, Any]:
        ensure_no_runtime_state(self.extra, path="resource_budget.extra")
        payload: dict[str, Any] = {
            "nproc": self.nproc,
            "mem": self.mem,
            "mem_mb": self.mem_mb,
            "queue": self.queue,
            "walltime_s": self.walltime_s,
            "partial": self.partial,
        }
        payload.update(dict(self.extra))
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ResourceBudget:
        if payload is None:
            return cls()
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("resource_budget must be a mapping")
        raw = dict(payload)
        nproc_raw = raw.pop("nproc", None)
        if nproc_raw is None:
            nproc_raw = raw.pop("cores", raw.pop("ncores", None))
        else:
            raw.pop("cores", None)
            raw.pop("ncores", None)
        mem_raw = raw.pop("mem", None)
        mem_mb_raw = raw.pop("mem_mb", None)
        memory_gb = raw.pop("memory_gb", None)
        if mem_mb_raw is None and memory_gb is not None:
            mem_mb_raw = int(memory_gb) * 1024
        queue = raw.pop("queue", None)
        walltime_s = raw.pop("walltime_s", None)
        partial = bool(raw.pop("partial", False))
        nproc: int | None = None
        if nproc_raw is not None:
            if isinstance(nproc_raw, bool) or not isinstance(nproc_raw, int):
                raise EnvelopeSerializationError("resource_budget.nproc must be an int")
            nproc = int(nproc_raw)
        mem_mb: int | None = None
        if mem_mb_raw is not None:
            mem_mb = int(mem_mb_raw)
        return cls(
            nproc=nproc,
            mem=_as_str(mem_raw, name="resource_budget.mem") or None,
            mem_mb=mem_mb,
            queue=_as_str(queue, name="resource_budget.queue") or None,
            walltime_s=int(walltime_s) if walltime_s is not None else None,
            partial=partial,
            extra=raw,
        )


@dataclass(frozen=True)
class ScientificClosure:
    """The frozen scientific closure a worker must honour (contract doc §5.3).

    ``task_kind``/``payload_schema``/``result_schema``/``algorithm_version``
    identify what the frozen :class:`cccp.calculation.requests.TaskRequest`
    will run and what result shape is expected; ``dependency_digests`` freezes
    per-workflow dependency / plugin digests.
    """

    task_kind: str
    payload_schema: int = 1
    result_schema: int = 1
    algorithm_version: str = ""
    dependency_digests: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_kind", _as_str(self.task_kind, name="task_kind"))
        object.__setattr__(self, "payload_schema", int(self.payload_schema))
        object.__setattr__(self, "result_schema", int(self.result_schema))
        object.__setattr__(
            self,
            "dependency_digests",
            _as_str_map(self.dependency_digests, name="dependency_digests"),
        )

    def to_dict(self) -> dict[str, Any]:
        ensure_no_runtime_state(self.dependency_digests, path="dependency_digests")
        return {
            "task_kind": self.task_kind,
            "payload_schema": self.payload_schema,
            "result_schema": self.result_schema,
            "algorithm_version": self.algorithm_version,
            "dependency_digests": dict(self.dependency_digests),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> ScientificClosure:
        if payload is None:
            return cls(task_kind="")
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("scientific_closure must be a mapping")
        return cls(
            task_kind=_as_str(payload.get("task_kind"), name="task_kind"),
            payload_schema=_as_int(payload.get("payload_schema"), name="payload_schema", default=1),
            result_schema=_as_int(payload.get("result_schema"), name="result_schema", default=1),
            algorithm_version=_as_str(payload.get("algorithm_version"), name="algorithm_version"),
            dependency_digests=_as_str_map(
                payload.get("dependency_digests"), name="dependency_digests"
            ),
        )


@dataclass(frozen=True)
class ExecutionError:
    """Typed error entry for a receipt (code + message + retryable)."""

    code: str
    message: str
    retryable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _as_str(self.code, name="error.code"))
        object.__setattr__(self, "message", _as_str(self.message, name="error.message"))
        object.__setattr__(self, "retryable", bool(self.retryable))

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionError:
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("error entry must be a mapping")
        return cls(
            code=_as_str(payload.get("code"), name="error.code", default="legacy"),
            message=_as_str(payload.get("message"), name="error.message"),
            retryable=bool(payload.get("retryable", False)),
        )


# ── ExecutionEnvelope ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionEnvelope:
    """Frozen execution identity for exactly one ``(job, attempt)``.

    Field whitelist = contract doc §5.1 plus the B06-1 additive fields
    (``release_digest``, ``step_identities``, ``protocol``, ``entrypoint``,
    ``required_capabilities``).  Only job/attempt/submission/release + owner/
    fencing + frozen plan + data bindings + budget + closure may appear — no
    platform UI identity (task display name, organization label, browser
    session) and never a ``TaskContext``.
    """

    envelope_version: int
    job_id: str
    attempt: int
    submission_id: str
    release_id: str
    owner: str
    fencing_token: str
    plan_digest: str
    plan_identity: str
    data_bindings: tuple[StorageBinding, ...] = ()
    resource_budget: ResourceBudget = field(default_factory=ResourceBudget)
    scientific_closure: ScientificClosure = field(default_factory=lambda: ScientificClosure(""))
    release_digest: str = ""
    step_identities: tuple[str, ...] = ()
    protocol: str | None = None
    entrypoint: str = DEFAULT_ENTRYPOINT
    required_capabilities: tuple[str, ...] = ()
    _migrated: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "envelope_version",
            _as_int(self.envelope_version, name="envelope_version", default=0),
        )
        object.__setattr__(self, "attempt", _as_int(self.attempt, name="attempt", default=1))
        for name in ("job_id", "submission_id", "release_id", "owner", "fencing_token"):
            object.__setattr__(self, name, _as_str(getattr(self, name), name=name))
        for name in ("plan_digest", "plan_identity", "release_digest"):
            object.__setattr__(self, name, _as_str(getattr(self, name), name=name))
        object.__setattr__(
            self, "step_identities", _as_str_tuple(self.step_identities, name="step_identities")
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _as_str_tuple(self.required_capabilities, name="required_capabilities"),
        )
        object.__setattr__(
            self,
            "data_bindings",
            tuple(self.data_bindings),
        )
        if self.attempt < 1:
            raise EnvelopeSerializationError("attempt must be >= 1")
        if self.entrypoint not in ENTRYPOINT_WHITELIST:
            raise UnsupportedCapabilityError(
                f"entrypoint {self.entrypoint!r} is not in the whitelist "
                f"{sorted(ENTRYPOINT_WHITELIST)}",
                unsupported=(self.entrypoint,),
            )
        if self.protocol is not None and self.protocol not in SUPPORTED_EXECUTION_PROTOCOLS:
            raise UnsupportedCapabilityError(
                f"execution protocol {self.protocol!r} is not supported "
                f"{sorted(SUPPORTED_EXECUTION_PROTOCOLS)}",
                unsupported=(self.protocol,),
            )
        missing = tuple(sorted(set(self.required_capabilities) - set(SUPPORTED_CAPABILITIES)))
        if missing:
            raise UnsupportedCapabilityError(
                f"unsupported required capabilities: {list(missing)}",
                unsupported=missing,
            )
        if not all(isinstance(b, StorageBinding) for b in self.data_bindings):
            raise EnvelopeSerializationError("data_bindings must contain StorageBinding entries")

    @property
    def schema_version(self) -> int:
        """Alias for :attr:`envelope_version` (the plan names it ``schema_version``)."""
        return self.envelope_version

    @property
    def migrated_from_legacy(self) -> bool:
        """True when this envelope was decoded from a pre-envelope legacy payload."""
        return self._migrated

    def selected_protocol(self) -> str | None:
        """Return the attempt-frozen execution protocol (``None`` = not yet selected)."""
        return self.protocol

    # -- protocol freeze per attempt --------------------------------------- #

    def select_protocol(
        self, protocol: str, *, release_id: str, release_digest: str = ""
    ) -> ExecutionEnvelope:
        """Freeze the execution protocol + release snapshot for THIS attempt.

        May be called at most once per attempt.  Re-selection on the same
        attempt (e.g. on resume) is rejected with :class:`AttemptProtocolError`;
        a new attempt must be opened via :meth:`advance_attempt` first.
        """
        if self.protocol is not None:
            raise AttemptProtocolError(
                f"attempt {self.attempt} already froze protocol {self.protocol!r}; "
                "re-selection on resume is forbidden (open a new attempt instead)"
            )
        if protocol not in SUPPORTED_EXECUTION_PROTOCOLS:
            raise UnsupportedCapabilityError(
                f"execution protocol {protocol!r} is not supported "
                f"{sorted(SUPPORTED_EXECUTION_PROTOCOLS)}",
                unsupported=(protocol,),
            )
        return replace(
            self,
            protocol=protocol,
            release_id=_as_str(release_id, name="release_id"),
            release_digest=_as_str(release_digest, name="release_digest"),
        )

    def advance_attempt(self, next_attempt: int, **overrides: Any) -> ExecutionEnvelope:
        """Open a NEW attempt, resetting the protocol snapshot for re-selection.

        ``next_attempt`` must be exactly ``self.attempt + 1``; any other value is
        rejected (attempt numbers are scheduler-owned and monotone).
        """
        if next_attempt != self.attempt + 1:
            raise AttemptProtocolError(
                f"attempt must advance exactly one step from {self.attempt} to "
                f"{self.attempt + 1}; got {next_attempt}"
            )
        reset: dict[str, Any] = {
            "attempt": next_attempt,
            "protocol": None,
            "release_id": "",
            "release_digest": "",
        }
        reset.update(overrides)
        return replace(self, **reset)

    def assert_capabilities(self, *, supported: frozenset[str] = SUPPORTED_CAPABILITIES) -> None:
        """Explicit failure for unsupported required capabilities."""
        assert_capabilities_supported(self, supported=supported)

    # -- wire form --------------------------------------------------------- #

    def to_dict(self) -> dict[str, Any]:
        for name in (
            "job_id",
            "submission_id",
            "release_id",
            "release_digest",
            "owner",
            "fencing_token",
            "plan_digest",
            "plan_identity",
            "protocol",
            "entrypoint",
            "data_bindings",
            "resource_budget",
            "scientific_closure",
            "required_capabilities",
            "step_identities",
        ):
            ensure_no_runtime_state(getattr(self, name), path=name)
        return {
            "envelope_version": self.envelope_version,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "submission_id": self.submission_id,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "plan_digest": self.plan_digest,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "protocol": self.protocol,
            "entrypoint": self.entrypoint,
            "required_capabilities": list(self.required_capabilities),
            "data_bindings": [binding.to_dict() for binding in self.data_bindings],
            "resource_budget": self.resource_budget.to_dict(),
            "scientific_closure": self.scientific_closure.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionEnvelope:
        """Strictly decode an envelope (unknown fields and runtime state rejected).

        Accepts both the explicit current version and the legacy implicit
        version (migrated via :func:`migrate_legacy_envelope`).
        """
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("envelope payload must be a mapping")
        raw = dict(payload)
        _reject_forbidden(raw, FORBIDDEN_ENVELOPE_FIELDS, what="envelope")
        ensure_no_runtime_state(raw, path="envelope")

        version = raw.get("envelope_version")
        if version is None:
            version = raw.get("schema_version", LEGACY_IMPLICIT_ENVELOPE_VERSION)
        version = _as_int(
            version, name="envelope_version", default=LEGACY_IMPLICIT_ENVELOPE_VERSION
        )
        if version > CURRENT_ENVELOPE_VERSION:
            raise EnvelopeVersionError(
                f"envelope_version {version} is newer than supported {CURRENT_ENVELOPE_VERSION}"
            )
        migrated = version < CURRENT_ENVELOPE_VERSION
        data = migrate_legacy_envelope(raw) if migrated else raw
        _reject_unknown(data, _ENVELOPE_FIELDS, what="envelope")

        data_bindings_raw = data.get("data_bindings") or ()
        if isinstance(data_bindings_raw, (str, bytes)) or not isinstance(
            data_bindings_raw, (list, tuple)
        ):
            raise EnvelopeSerializationError("data_bindings must be a list")
        bindings = tuple(StorageBinding.from_dict(item) for item in data_bindings_raw)

        envelope = cls(
            envelope_version=CURRENT_ENVELOPE_VERSION if migrated else version,
            job_id=_as_str(data.get("job_id"), name="job_id"),
            attempt=_as_int(data.get("attempt"), name="attempt", default=1),
            submission_id=_as_str(data.get("submission_id"), name="submission_id"),
            release_id=_as_str(data.get("release_id"), name="release_id"),
            release_digest=_as_str(data.get("release_digest"), name="release_digest"),
            owner=_as_str(data.get("owner"), name="owner"),
            fencing_token=_as_str(data.get("fencing_token"), name="fencing_token"),
            plan_digest=_as_str(data.get("plan_digest"), name="plan_digest"),
            plan_identity=_as_str(data.get("plan_identity"), name="plan_identity"),
            step_identities=_as_str_tuple(data.get("step_identities"), name="step_identities"),
            protocol=data.get("protocol"),
            entrypoint=_as_str(
                data.get("entrypoint"), name="entrypoint", default=DEFAULT_ENTRYPOINT
            ),
            required_capabilities=_as_str_tuple(
                data.get("required_capabilities"), name="required_capabilities"
            ),
            data_bindings=bindings,
            resource_budget=ResourceBudget.from_dict(data.get("resource_budget")),
            scientific_closure=ScientificClosure.from_dict(data.get("scientific_closure")),
        )
        object.__setattr__(envelope, "_migrated", migrated)
        return envelope


def assert_resume_does_not_reselect(
    envelope: ExecutionEnvelope, *, attempt: int, protocol: str
) -> None:
    """Reject re-selecting a protocol when resuming an already-frozen attempt.

    A resume must carry the same attempt (and therefore the same protocol); a
    caller may only pass a *different* protocol when opening a new attempt.
    """
    if (
        attempt == envelope.attempt
        and envelope.protocol is not None
        and protocol != envelope.protocol
    ):
        raise AttemptProtocolError(
            f"resume of attempt {attempt} re-selected protocol {protocol!r} "
            f"(frozen protocol is {envelope.protocol!r}); only a new attempt may re-select"
        )


# ── ExecutionReceipt ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExecutionReceipt:
    """Observation/completion receipt a worker writes after executing an attempt.

    Field whitelist = contract doc §5.2 (``receipt_version``, job/attempt/
    submission, ``state``, ``result_digest``, ``errors``, ``written_at``) plus
    the B06-1 additive observation fields (release/owner/fencing, plan/step
    identity, StorageBinding, evidence digests).  ``errors`` accepts plain
    strings (the frozen ``list[str]`` shape) or typed :class:`ExecutionError`
    entries; a plain string is kept verbatim on round-trip.
    """

    receipt_version: int
    job_id: str
    attempt: int
    submission_id: str
    state: str
    result_digest: str = ""
    errors: tuple[ExecutionError | str, ...] = ()
    written_at: str = ""
    release_id: str = ""
    release_digest: str = ""
    owner: str = ""
    fencing_token: str = ""
    plan_identity: str = ""
    step_identities: tuple[str, ...] = ()
    data_bindings: tuple[StorageBinding, ...] = ()
    evidence_digests: Mapping[str, str] = field(default_factory=dict)
    _migrated: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "receipt_version",
            _as_int(self.receipt_version, name="receipt_version", default=0),
        )
        object.__setattr__(self, "attempt", _as_int(self.attempt, name="attempt", default=1))
        for name in ("job_id", "submission_id", "state", "release_id", "owner", "fencing_token"):
            object.__setattr__(self, name, _as_str(getattr(self, name), name=name))
        for name in ("result_digest", "written_at", "release_digest", "plan_identity"):
            object.__setattr__(self, name, _as_str(getattr(self, name), name=name))
        object.__setattr__(
            self, "step_identities", _as_str_tuple(self.step_identities, name="step_identities")
        )
        object.__setattr__(self, "data_bindings", tuple(self.data_bindings))
        object.__setattr__(
            self, "evidence_digests", _as_str_map(self.evidence_digests, name="evidence_digests")
        )
        if self.attempt < 1:
            raise EnvelopeSerializationError("attempt must be >= 1")
        if self.state not in RECEIPT_STATES:
            raise EnvelopeSerializationError(
                f"state {self.state!r} not in {sorted(RECEIPT_STATES)}"
            )
        if not all(isinstance(b, StorageBinding) for b in self.data_bindings):
            raise EnvelopeSerializationError("data_bindings must contain StorageBinding entries")
        for entry in self.errors:
            if not isinstance(entry, (ExecutionError, str)):
                raise EnvelopeSerializationError("errors entries must be str or ExecutionError")

    @property
    def schema_version(self) -> int:
        """Alias for :attr:`receipt_version`."""
        return self.receipt_version

    @property
    def migrated_from_legacy(self) -> bool:
        """True when this receipt was decoded from a legacy payload."""
        return self._migrated

    def to_dict(self) -> dict[str, Any]:
        ensure_no_runtime_state(self.errors, path="errors")
        ensure_no_runtime_state(self.evidence_digests, path="evidence_digests")
        return {
            "receipt_version": self.receipt_version,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "submission_id": self.submission_id,
            "state": self.state,
            "result_digest": self.result_digest,
            "errors": [
                entry.to_dict() if isinstance(entry, ExecutionError) else entry
                for entry in self.errors
            ],
            "written_at": self.written_at,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "owner": self.owner,
            "fencing_token": self.fencing_token,
            "plan_identity": self.plan_identity,
            "step_identities": list(self.step_identities),
            "data_bindings": [binding.to_dict() for binding in self.data_bindings],
            "evidence_digests": dict(self.evidence_digests),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ExecutionReceipt:
        if not isinstance(payload, Mapping):
            raise EnvelopeSerializationError("receipt payload must be a mapping")
        raw = dict(payload)
        _reject_forbidden(raw, FORBIDDEN_RECEIPT_FIELDS, what="receipt")
        ensure_no_runtime_state(raw, path="receipt")

        version = raw.get("receipt_version")
        if version is None:
            version = raw.get("schema_version", LEGACY_IMPLICIT_RECEIPT_VERSION)
        version = _as_int(version, name="receipt_version", default=LEGACY_IMPLICIT_RECEIPT_VERSION)
        if version > CURRENT_RECEIPT_VERSION:
            raise EnvelopeVersionError(
                f"receipt_version {version} is newer than supported {CURRENT_RECEIPT_VERSION}"
            )
        migrated = version < CURRENT_RECEIPT_VERSION
        data = migrate_legacy_receipt(raw) if migrated else raw
        _reject_unknown(data, _RECEIPT_FIELDS, what="receipt")

        errors_raw = data.get("errors") or ()
        if isinstance(errors_raw, (str, bytes)) or not isinstance(errors_raw, (list, tuple)):
            raise EnvelopeSerializationError("errors must be a list")
        errors: list[ExecutionError | str] = []
        for entry in errors_raw:
            if isinstance(entry, str):
                errors.append(entry)
            elif isinstance(entry, Mapping):
                errors.append(ExecutionError.from_dict(entry))
            else:
                raise EnvelopeSerializationError("errors entries must be str or mapping")

        bindings_raw = data.get("data_bindings") or ()
        if isinstance(bindings_raw, (str, bytes)) or not isinstance(bindings_raw, (list, tuple)):
            raise EnvelopeSerializationError("data_bindings must be a list")
        bindings = tuple(StorageBinding.from_dict(item) for item in bindings_raw)

        receipt = cls(
            receipt_version=CURRENT_RECEIPT_VERSION if migrated else version,
            job_id=_as_str(data.get("job_id"), name="job_id"),
            attempt=_as_int(data.get("attempt"), name="attempt", default=1),
            submission_id=_as_str(data.get("submission_id"), name="submission_id"),
            state=_as_str(data.get("state"), name="state"),
            result_digest=_as_str(data.get("result_digest"), name="result_digest"),
            errors=tuple(errors),
            written_at=_as_str(data.get("written_at"), name="written_at"),
            release_id=_as_str(data.get("release_id"), name="release_id"),
            release_digest=_as_str(data.get("release_digest"), name="release_digest"),
            owner=_as_str(data.get("owner"), name="owner"),
            fencing_token=_as_str(data.get("fencing_token"), name="fencing_token"),
            plan_identity=_as_str(data.get("plan_identity"), name="plan_identity"),
            step_identities=_as_str_tuple(data.get("step_identities"), name="step_identities"),
            data_bindings=bindings,
            evidence_digests=_as_str_map(data.get("evidence_digests"), name="evidence_digests"),
        )
        object.__setattr__(receipt, "_migrated", migrated)
        return receipt
