"""Unified machine-output contract — JSON envelope, exit codes, error objects.

B08-1 (plan ``.omo/plans/acp-parallel-plan-2-services.md`` todo 42).  Every
machine-facing ACP entrypoint (the CLI ``--json`` mode today; MCP in todo 43;
later API/MCP clients) must emit **one** stable shape so that scripts,
agents and the MCP bridge never parse prose or guess an exit code from a log
line.

The contract has three frozen pieces
-------------------------------------

1. **Result envelope** (:class:`MachineResult`) — operation identity, status,
   ``dry_run`` flag, optional structured ``data`` and an optional
   :class:`ErrorObject`.  ``schema_version`` is versioned so consumers can
   branch on shape, not on presence of keys.
2. **Error object** (:class:`ErrorObject`) — a *stable* ``code`` from
   :class:`ErrorCode`, a *structured* ``params`` mapping, a
   **safe-to-display** ``message`` (host paths / secrets already redacted),
   a ``retryable`` flag and a ``diagnostic_ref`` (trace/diagnostic handle).
   This is the object B08-6 (structured logging / diagnostic export) and the
   todo-24 review surfaces reference — they link to a trace, they do not
   re-derive error semantics from prose.
3. **Exit-code table** (:class:`ExitCode`) — ``0/1/2/130`` keep their legacy
   meaning exactly (success / runtime failure / usage / ``SIGINT``); the new
   categories (unavailable / conflict / denied / internal) take the next
   stable integers so a caller can branch on the process status without
   parsing output.

Zero-side-effect dry-run
------------------------

:func:`dry_run_result` builds a success envelope that advertises
``executed=False``, ``metering_consumed=False`` and ``transfers_attempted=False``.
The CLI's ``--dry-run`` path only *plans / validates / estimates*: it never
calls ``cccp.calculation.run_*``, never launches a scientific binary and never
reserves core-hours.  ``tests/test_services_machine_output.py`` asserts this
with a mock that fails loudly if any execution entrypoint is reached.

Dependency direction
--------------------

This module imports **stdlib only** — no ``acp`` import (allowed dependency
rule ``acp_services → acp`` is forbidden).  Where a semantic is owned by a
legacy contract it is *re-derived* here and cross-referenced by ``file:line``
in the relevant docstring rather than imported.

Author: ACP Services (B lane, todo 42).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MACHINE_SCHEMA_VERSION",
    "ErrorCategory",
    "ExitCode",
    "ErrorCode",
    "ErrorSpec",
    "ErrorObject",
    "MachineResult",
    "ERROR_CATALOG",
    "error_object",
    "error_from_exception",
    "exit_code_for",
    "error_spec",
    "default_error_for_exit",
    "dry_run_result",
    "redact",
    "redact_value",
]

#: Wire schema of the machine envelope.  Bump only on a breaking change; the
#: matching consumer change is registered in the plan's contract ledger.
MACHINE_SCHEMA_VERSION = 1

#: Placeholder substituted for a redacted host path.
REDACTED_PATH = "<redacted-path>"
#: Placeholder substituted for a redacted secret value.
REDACTED_SECRET = "<redacted>"


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


class ExitCode:
    """Stable process exit-code table (legacy-compatible + typed categories).

    The first block is **frozen legacy behaviour** and may never change
    meaning:

    * ``OK`` 0 — success (also the dry-run success status).
    * ``FAILED`` 1 — scientific/runtime failure
      (``src/acp/cli.py`` handlers return ``1`` on a failed result/exception).
    * ``USAGE`` 2 — invalid CLI usage / invalid input; argparse itself exits
      ``2`` on a parse error.
    * ``INTERRUPTED`` 130 — ``KeyboardInterrupt``
      (``src/acp/cli.py`` handlers return ``130`` on ``SIGINT``).

    The second block is the typed category extension (``3``…``6``) that maps
    one-to-one onto :class:`ErrorCategory`.
    """

    OK = 0
    FAILED = 1
    USAGE = 2
    UNAVAILABLE = 3
    CONFLICT = 4
    DENIED = 5
    INTERNAL = 6
    INTERRUPTED = 130

    #: Legacy codes whose meaning is frozen for backward compatibility.
    LEGACY: tuple[int, ...] = (OK, FAILED, USAGE, INTERRUPTED)

    @classmethod
    def all_values(cls) -> tuple[int, ...]:
        return (
            cls.OK,
            cls.FAILED,
            cls.USAGE,
            cls.UNAVAILABLE,
            cls.CONFLICT,
            cls.DENIED,
            cls.INTERNAL,
            cls.INTERRUPTED,
        )


class ErrorCategory(str):
    """Coarse error class that owns an :class:`ExitCode`."""

    OK = "ok"
    FAILED = "failed"
    USAGE = "usage"
    UNAVAILABLE = "unavailable"
    CONFLICT = "conflict"
    DENIED = "denied"
    INTERNAL = "internal"
    INTERRUPTED = "interrupted"

    ALL = frozenset({OK, FAILED, USAGE, UNAVAILABLE, CONFLICT, DENIED, INTERNAL, INTERRUPTED})


#: Category → exit code (total; every category has exactly one code).
_CATEGORY_EXIT: dict[str, int] = {
    ErrorCategory.OK: ExitCode.OK,
    ErrorCategory.FAILED: ExitCode.FAILED,
    ErrorCategory.USAGE: ExitCode.USAGE,
    ErrorCategory.UNAVAILABLE: ExitCode.UNAVAILABLE,
    ErrorCategory.CONFLICT: ExitCode.CONFLICT,
    ErrorCategory.DENIED: ExitCode.DENIED,
    ErrorCategory.INTERNAL: ExitCode.INTERNAL,
    ErrorCategory.INTERRUPTED: ExitCode.INTERRUPTED,
}


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


class ErrorCode(str):
    """Stable machine-readable error vocabulary.

    Codes are namespaced strings, never translated (B08-6 i18n rule) and never
    derived from prose.  Each code belongs to exactly one
    :class:`ErrorCategory` and carries a default ``retryable`` flag.
    """

    INPUT_INVALID = "input_invalid"
    USAGE_ERROR = "usage_error"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    RESOURCE_NOT_READY = "resource_not_ready"
    REVISION_CONFLICT = "revision_conflict"
    ACCESS_DENIED = "access_denied"
    NOT_CONVERGED = "not_converged"
    TIMEOUT = "timeout"
    PARSE_FAILURE = "parse_failure"
    BACKEND_FAILURE = "backend_failure"
    RUNTIME_FAILURE = "runtime_failure"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    INTERNAL_ERROR = "internal_error"
    UNKNOWN = "unknown_error"

    ALL = frozenset(
        {
            INPUT_INVALID,
            USAGE_ERROR,
            UNSUPPORTED_CAPABILITY,
            BACKEND_UNAVAILABLE,
            RESOURCE_UNAVAILABLE,
            RESOURCE_NOT_READY,
            REVISION_CONFLICT,
            ACCESS_DENIED,
            NOT_CONVERGED,
            TIMEOUT,
            PARSE_FAILURE,
            BACKEND_FAILURE,
            RUNTIME_FAILURE,
            CANCELLED,
            INTERRUPTED,
            INTERNAL_ERROR,
            UNKNOWN,
        }
    )


@dataclass(frozen=True)
class ErrorSpec:
    """Frozen definition of one error code (category + retryability)."""

    code: str
    category: str
    retryable: bool

    def __post_init__(self) -> None:
        if self.category not in ErrorCategory.ALL:
            raise ValueError(f"unknown error category {self.category!r}")
        if self.code not in ErrorCode.ALL:
            raise ValueError(f"unknown error code {self.code!r}")

    @property
    def exit_code(self) -> int:
        return _CATEGORY_EXIT[self.category]


def _spec(code: str, category: str, retryable: bool = False) -> ErrorSpec:
    return ErrorSpec(code=code, category=category, retryable=retryable)


#: The single source of truth for code → category → exit code + retryability.
ERROR_CATALOG: dict[str, ErrorSpec] = {
    # usage / input — not retryable
    ErrorCode.INPUT_INVALID: _spec(ErrorCode.INPUT_INVALID, ErrorCategory.USAGE),
    ErrorCode.USAGE_ERROR: _spec(ErrorCode.USAGE_ERROR, ErrorCategory.USAGE),
    ErrorCode.UNSUPPORTED_CAPABILITY: _spec(ErrorCode.UNSUPPORTED_CAPABILITY, ErrorCategory.USAGE),
    # unavailable / not-ready — retryable (transient prerequisite)
    ErrorCode.BACKEND_UNAVAILABLE: _spec(
        ErrorCode.BACKEND_UNAVAILABLE, ErrorCategory.UNAVAILABLE, True
    ),
    ErrorCode.RESOURCE_UNAVAILABLE: _spec(
        ErrorCode.RESOURCE_UNAVAILABLE, ErrorCategory.UNAVAILABLE, True
    ),
    ErrorCode.RESOURCE_NOT_READY: _spec(
        ErrorCode.RESOURCE_NOT_READY, ErrorCategory.UNAVAILABLE, True
    ),
    # conflict — retryable with a refreshed revision
    ErrorCode.REVISION_CONFLICT: _spec(ErrorCode.REVISION_CONFLICT, ErrorCategory.CONFLICT, True),
    # denied — policy decision, never silently retried
    ErrorCode.ACCESS_DENIED: _spec(ErrorCode.ACCESS_DENIED, ErrorCategory.DENIED),
    # scientific / runtime failure — the legacy exit-1 category
    ErrorCode.NOT_CONVERGED: _spec(ErrorCode.NOT_CONVERGED, ErrorCategory.FAILED),
    ErrorCode.TIMEOUT: _spec(ErrorCode.TIMEOUT, ErrorCategory.FAILED, True),
    ErrorCode.PARSE_FAILURE: _spec(ErrorCode.PARSE_FAILURE, ErrorCategory.FAILED),
    ErrorCode.BACKEND_FAILURE: _spec(ErrorCode.BACKEND_FAILURE, ErrorCategory.FAILED),
    ErrorCode.RUNTIME_FAILURE: _spec(ErrorCode.RUNTIME_FAILURE, ErrorCategory.FAILED),
    ErrorCode.CANCELLED: _spec(ErrorCode.CANCELLED, ErrorCategory.FAILED),
    # interruption — SIGINT, legacy 130
    ErrorCode.INTERRUPTED: _spec(ErrorCode.INTERRUPTED, ErrorCategory.INTERRUPTED),
    # internal programming error
    ErrorCode.INTERNAL_ERROR: _spec(ErrorCode.INTERNAL_ERROR, ErrorCategory.INTERNAL),
    # conservative fallback
    ErrorCode.UNKNOWN: _spec(ErrorCode.UNKNOWN, ErrorCategory.FAILED),
}


def error_spec(code: str) -> ErrorSpec:
    """Return the frozen :class:`ErrorSpec` for *code* (fallback: UNKNOWN)."""
    return ERROR_CATALOG.get(str(code), ERROR_CATALOG[ErrorCode.UNKNOWN])


def exit_code_for(code: str) -> int:
    """Map a stable error *code* to its process exit code."""
    return error_spec(code).exit_code


#: Exception class-name → error code.  Matched by ``type(exc).__name__`` so this
#: stdlib-only module never imports the defining packages (``cccp``/``acp``).
_EXCEPTION_CODE_BY_NAME: dict[str, str] = {
    "KeyboardInterrupt": ErrorCode.INTERRUPTED,
    "BackendUnavailableError": ErrorCode.BACKEND_UNAVAILABLE,
    "UnsupportedCapabilityError": ErrorCode.UNSUPPORTED_CAPABILITY,
    "TaskInputError": ErrorCode.INPUT_INVALID,
    "FileNotFoundError": ErrorCode.INPUT_INVALID,
    "NotADirectoryError": ErrorCode.INPUT_INVALID,
    "IsADirectoryError": ErrorCode.INPUT_INVALID,
    "PermissionError": ErrorCode.ACCESS_DENIED,
    "TimeoutError": ErrorCode.TIMEOUT,
    "RevisionConflictError": ErrorCode.REVISION_CONFLICT,
    "ConflictError": ErrorCode.REVISION_CONFLICT,
}

#: Exit code → conservative error code (for wrapping a handler that returned an
#: int without a richer object).  Keeps the legacy 0/1/2/130 mapping total.
_EXIT_CODE_ERROR: dict[int, str] = {
    ExitCode.FAILED: ErrorCode.RUNTIME_FAILURE,
    ExitCode.USAGE: ErrorCode.USAGE_ERROR,
    ExitCode.UNAVAILABLE: ErrorCode.RESOURCE_UNAVAILABLE,
    ExitCode.CONFLICT: ErrorCode.REVISION_CONFLICT,
    ExitCode.DENIED: ErrorCode.ACCESS_DENIED,
    ExitCode.INTERNAL: ErrorCode.INTERNAL_ERROR,
    ExitCode.INTERRUPTED: ErrorCode.INTERRUPTED,
}


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

#: Absolute POSIX host path (``/a/b`` or ``~/a/b``) — replaced wholesale.
_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])~?/(?:[A-Za-z0-9._\-+@%]+/)*[A-Za-z0-9._\-+@%]*")
#: ``key=value`` / ``key: value`` where key smells like a secret.
_SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(password|passwd|passphrase|token|secret|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|client[_-]?secret|authorization|credential)\b(\s*[:=]\s*)(\S+)"
)
#: ``Bearer <token>``.
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+")
#: URL query parameters carrying secrets.
_URL_SECRET_RE = re.compile(
    r"(?i)([?&](?:token|key|secret|password|passwd|sig|signature|api[_-]?key)=)[^&\s]+"
)
#: URL userinfo ``scheme://user:pass@host``.
_URL_USERINFO_RE = re.compile(r"(?i)([a-z][a-z0-9+.\-]*://)[^/@\s]+@")
#: PEM private key blocks (may span lines).
_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
)
#: Long opaque token-ish runs (>=32 chars of base64-ish) — last-resort net.
#: Pure-lowercase-hex digests (sha256 plan/step identities, ``v2:`` prefixes
#: carry their own hex) are *not* secrets and survive so consumers can keep
#: operation/diagnostic identity.
_OPAQUE_RE = re.compile(
    r"\b(?![0-9a-f]{32,}\b)"
    r"(?!(?:op|operation|job|task)_[0-9a-f]{16,}\b)"
    r"[A-Za-z0-9_\-]{32,}\b"
)


#: Public wire identifiers (MCP tool names, negotiation extension ids) are
#: published on purpose and are longer than the opaque-token net.  They are not
#: secrets, so the net must not rewrite them (M2-01c publishes ten research tool
#: names in the capability/projection payloads).
_WIRE_IDENTIFIER_RE = re.compile(
    r"^acp[_a-z0-9]*$|^rph[_a-z0-9]*$|^acp\.[a-z0-9_.]+$|^rph\.[a-z0-9_.]+$"
)


def _redact_secrets(text: str) -> str:
    value = str(text)
    value = _PEM_RE.sub(REDACTED_SECRET, value)
    value = _URL_USERINFO_RE.sub(r"\1" + REDACTED_SECRET + "@", value)
    value = _BEARER_RE.sub("Bearer " + REDACTED_SECRET, value)
    value = _URL_SECRET_RE.sub(r"\1" + REDACTED_SECRET, value)
    value = _SECRET_ASSIGN_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED_SECRET}", value)
    value = _OPAQUE_RE.sub(
        lambda m: m.group(0) if _WIRE_IDENTIFIER_RE.match(m.group(0)) else REDACTED_SECRET,
        value,
    )
    return value


def redact(text: str) -> str:
    """Return *text* with host paths and secrets replaced by placeholders.

    Idempotent and deterministic; safe to call on user-visible ``message`` and
    on every structured string before serialization.  Ordering matters: secret
    assignments are handled before the opaque-token net so the key name is kept
    readable while the value is dropped.
    """
    return _ABSOLUTE_PATH_RE.sub(REDACTED_PATH, _redact_secrets(text))


def redact_value(value: Any) -> Any:
    """Recursively redact every string in *value* (mappings/sequences preserved).

    Mapping **keys** are left untouched — they are our own structured parameter
    names — while every string value (and every string inside nested
    containers) is passed through :func:`redact`.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Mapping):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Error object
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorObject:
    """Machine-readable, safe-to-display error.

    Attributes:
        code: A stable :class:`ErrorCode` value (never translated).
        message: Human-readable, **already redacted** message.
        retryable: Whether an identical request could succeed later.
        params: Structured, machine-consumable context (``{"field": ...}``);
            string values are redacted on serialization.
        diagnostic_ref: Opaque trace/diagnostic handle (e.g. a trace id or a
            diagnostic-export URI) consumed by B08-6 / todo 24; ``None`` when
            no diagnostic was produced.
    """

    code: str
    message: str
    retryable: bool = False
    params: Mapping[str, Any] = field(default_factory=dict)
    diagnostic_ref: str | None = None

    def __post_init__(self) -> None:
        spec = error_spec(self.code)
        object.__setattr__(self, "code", spec.code)
        object.__setattr__(self, "message", redact(self.message))
        object.__setattr__(self, "retryable", bool(self.retryable))
        object.__setattr__(self, "params", dict(self.params))
        if self.diagnostic_ref is not None:
            object.__setattr__(self, "diagnostic_ref", _redact_secrets(str(self.diagnostic_ref)))

    @property
    def category(self) -> str:
        return error_spec(self.code).category

    @property
    def exit_code(self) -> int:
        return error_spec(self.code).exit_code

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
            "retryable": self.retryable,
            "params": redact_value(dict(self.params)),
            "diagnostic_ref": self.diagnostic_ref,
        }


def error_object(
    code: str,
    message: str | None = None,
    *,
    params: Mapping[str, Any] | None = None,
    retryable: bool | None = None,
    diagnostic_ref: str | None = None,
) -> ErrorObject:
    """Build an :class:`ErrorObject`, filling defaults from the catalog.

    ``retryable`` defaults to the code's catalog value; the explicit argument
    lets a caller override it when the situation is known (e.g. a backend
    failure that is *not* worth retrying).
    """
    spec = error_spec(code)
    resolved_message = message if message is not None else f"{spec.category} error: {spec.code}"
    resolved_retryable = spec.retryable if retryable is None else retryable
    return ErrorObject(
        code=spec.code,
        message=resolved_message,
        retryable=resolved_retryable,
        params=params or {},
        diagnostic_ref=diagnostic_ref,
    )


def error_from_exception(exc: BaseException, *, diagnostic_ref: str | None = None) -> ErrorObject:
    """Classify *exc* by class name into a stable error object (no imports).

    ``KeyboardInterrupt`` maps to the legacy ``interrupted`` (130) category;
    known scientific/prerequisite exceptions map to their codes; everything
    else is a conservative ``internal_error`` with the (redacted) string form
    of the exception.
    """
    name = type(exc).__name__
    code = _EXCEPTION_CODE_BY_NAME.get(name, ErrorCode.INTERNAL_ERROR)
    return error_object(
        code,
        str(exc) or name,
        params={"exception": name},
        diagnostic_ref=diagnostic_ref,
    )


def default_error_for_exit(exit_code: int) -> ErrorObject | None:
    """Best-effort error object for a bare handler exit code.

    Returns ``None`` for ``0``; otherwise the conservative code for the
    category owning *exit_code* (``runtime_failure`` for the legacy ``1``).
    """
    if exit_code == ExitCode.OK:
        return None
    code = _EXIT_CODE_ERROR.get(int(exit_code), ErrorCode.RUNTIME_FAILURE)
    return error_object(code, f"command exited with status {int(exit_code)}")


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineResult:
    """Frozen machine-output envelope.

    Attributes:
        operation: Operation identity (the workflow/entrypoint name).
        status: ``"ok"`` or ``"error"``.
        exit_code: Process exit code (see :class:`ExitCode`).
        dry_run: ``True`` when only plan/validate/estimate ran.
        data: Structured payload (already JSON-safe; redacted on serialization).
        error: :class:`ErrorObject` when ``status == "error"``, else ``None``.
        operation_identity: Optional stable plan/operation digest.
        diagnostic_ref: Envelope-level trace/diagnostic handle.
        schema_version: Envelope wire version.
    """

    operation: str
    status: str
    exit_code: int = ExitCode.OK
    dry_run: bool = False
    data: Mapping[str, Any] | None = None
    error: ErrorObject | None = None
    operation_identity: str | None = None
    diagnostic_ref: str | None = None
    schema_version: int = MACHINE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in ("ok", "error"):
            raise ValueError(f"unknown machine status {self.status!r}")
        if self.status == "ok" and self.error is not None:
            raise ValueError("a successful result must not carry an error object")
        if self.status == "error" and self.error is None:
            raise ValueError("a failed result must carry an error object")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "operation": self.operation,
            "status": self.status,
            "exit_code": self.exit_code,
            "dry_run": self.dry_run,
            "operation_identity": self.operation_identity,
            "diagnostic_ref": self.diagnostic_ref,
            "data": redact_value(dict(self.data)) if self.data is not None else None,
            "error": self.error.to_dict() if self.error is not None else None,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize with a stable key order and ``ensure_ascii=False``."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


def dry_run_result(
    operation: str,
    *,
    plan: Mapping[str, Any] | Sequence[Any] | None = None,
    estimate: Mapping[str, Any] | None = None,
    operation_identity: str | None = None,
    diagnostic_ref: str | None = None,
) -> MachineResult:
    """Build the zero-side-effect dry-run success envelope.

    The returned ``data`` explicitly asserts what did **not** happen:
    ``executed=False``, ``metering_consumed=False``,
    ``transfers_attempted=False`` and ``core_hours_reserved=0``.  The dry-run
    path in the CLI only plans / validates / estimates; this envelope is the
    machine-verifiable statement of that guarantee.
    """
    data: dict[str, Any] = {
        "executed": False,
        "metering_consumed": False,
        "transfers_attempted": False,
        "core_hours_reserved": 0,
        "plan": plan if plan is not None else {},
        "estimate": dict(estimate) if estimate else {},
    }
    return MachineResult(
        operation=operation,
        status="ok",
        exit_code=ExitCode.OK,
        dry_run=True,
        data=data,
        operation_identity=operation_identity,
        diagnostic_ref=diagnostic_ref,
    )
