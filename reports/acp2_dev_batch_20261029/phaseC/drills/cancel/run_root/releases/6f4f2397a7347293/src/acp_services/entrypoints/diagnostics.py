"""Structured logging, redaction and diagnostic export — B08-6 (todo 47).

The machine *result* contract (:mod:`acp_services.entrypoints.machine_output`)
says what a caller gets back; this module says how the service **observes**
itself without ever becoming an authority over science or recovery.

Five frozen pieces
------------------

1. **Structured JSON-line logging** (:class:`JsonLogFormatter`) — every line is
   one JSON object with a stable field set: ``time`` / ``level`` / ``logger`` /
   ``message`` / business correlation ``job_id`` / ``attempt`` / ``operation`` /
   ``error_code`` and the diagnostic ``trace_id`` / ``span_id``.  Host paths,
   secrets and URL query parameters are redacted **by default** (reusing the
   single redaction implementation in :mod:`~acp_services.entrypoints.machine_output`
   — no second copy of the constants/regexes).
2. **Business correlation vs diagnostic trace** — :class:`BusinessContext` is
   the *persistent* identity (a retry keeps the same ``job_id``/``operation``
   and only advances ``attempt``); :class:`TraceContext` is *diagnostic only*
   and may change on every attempt.  A retry opens a **new** trace that links
   back to the existing operation — the trace never rewrites business state.
3. **Diagnostic export** — :func:`build_diagnostic_bundle` assembles a redacted
   package (logs + system capabilities + recent operations) and
   :func:`export_diagnostic_bundle` writes it atomically.  Paths and secrets are
   stripped from the whole bundle.
4. **Optional OpenTelemetry adapter** — a :class:`OtelAdapter` Protocol with a
   default :class:`NoOpOtelAdapter`.  OTel is **off by default**, imports no OTel
   SDK and starts no resident service; activation is an explicit
   :func:`enable_otel` call.
5. **i18n invariance** — :func:`canonical_request` / :func:`request_hash` /
   :func:`request_plan` hash only the stable, language-independent request
   identity (scientific enum tokens stay verbatim).  :func:`localize_error`
   translates the human ``message`` only — the stable ``code`` and structured
   ``params`` are never translated, so switching zh/en cannot change a request
   hash, a plan or a scientific result.

Dependency direction
-------------------

This module imports **stdlib** plus its sibling
:mod:`acp_services.entrypoints.machine_output`; it never imports ``acp`` (the
``acp_services → acp`` dependency is forbidden — see
``tests/baseline/services-evidence/public_allowlist.md``).  Logging, traces and
metric-style observations are **never** a settlement, metering or recovery
authority: only the durable store/CAS owns those decisions.

Author: ACP Services (B lane, todo 47).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, Protocol, runtime_checkable

from acp_services.entrypoints.machine_output import REDACTED_SECRET, redact, redact_value

__all__ = [
    "LOG_SCHEMA_VERSION",
    "DIAGNOSTIC_SCHEMA_VERSION",
    "LOG_RECORD_KEYS",
    "ACP_LOG_FORMAT_ENV",
    "ACP_OTEL_ENABLED_ENV",
    "ACP_OTEL_ENDPOINT_ENV",
    "SUPPORTED_LOCALES",
    "DEFAULT_LOCALE",
    "BusinessContext",
    "TraceContext",
    "JsonLogFormatter",
    "structured_logging_enabled",
    "configure_structured_logging",
    "new_trace",
    "canonical_request",
    "request_hash",
    "request_plan",
    "localize_error",
    "OtelAdapter",
    "NoOpOtelAdapter",
    "otel_enabled",
    "otel_settings",
    "get_otel_adapter",
    "enable_otel",
    "disable_otel",
    "system_capabilities",
    "build_diagnostic_bundle",
    "diagnostic_bundle_json",
    "export_diagnostic_bundle",
]

#: Wire version of a structured log line.  Bump only on a breaking field change.
LOG_SCHEMA_VERSION: Final = 1
#: Wire version of the diagnostic bundle.
DIAGNOSTIC_SCHEMA_VERSION: Final = 1
#: Diagnostic bundle discriminator.
DIAGNOSTIC_KIND: Final = "acp_services_diagnostics"

#: The fixed top-level key set of one JSON log line (nulls kept so a consumer
#: can branch on shape, not on presence of keys).
LOG_RECORD_KEYS: Final[tuple[str, ...]] = (
    "schema_version",
    "time",
    "level",
    "logger",
    "message",
    "job_id",
    "attempt",
    "operation",
    "error_code",
    "trace_id",
    "span_id",
)

#: Env switch that opts the CLI/server into structured (JSON-line) logging.
#: Any other value keeps the legacy text format exactly.
ACP_LOG_FORMAT_ENV: Final = "ACP_LOG_FORMAT"
#: Explicit OTel opt-in switch (read only for reporting; OTel stays off unless a
#: caller registers an adapter via :func:`enable_otel`).
ACP_OTEL_ENABLED_ENV: Final = "ACP_OTEL_ENABLED"
#: OTLP endpoint hint (reported, never dialed by this module).
ACP_OTEL_ENDPOINT_ENV: Final = "ACP_OTEL_ENDPOINT"

#: Locales the error-message localiser ships.
SUPPORTED_LOCALES: Final[tuple[str, ...]] = ("en", "zh")
DEFAULT_LOCALE: Final = "en"

#: Fields carrying human-facing/translatable text or a locale selector.  They are
#: excluded from the scientific request identity: switching zh/en must not move
#: the hash, the plan or the scientific result.  Scientific enum tokens
#: (``method`` / ``basis`` / ``workflow`` / ``charge`` / ``multiplicity`` /
#: ``steps`` …) are **not** in this set and stay verbatim.
_I18N_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "locale",
        "language",
        "lang",
        "i18n",
        "display",
        "display_name",
        "label",
        "title",
        "message",
        "note",
        "comment",
        "description",
        "summary",
        "error_message",
    }
)

#: Workflow → planned step names (mirrors the CLI dry-run plan; planning only).
_PLAN_STEPS: Final[dict[str, tuple[str, ...]]] = {
    "singlepoint": ("single_point",),
    "optimize": ("optimize",),
    "frequency": ("frequency",),
    "scan": ("scan",),
    "casscf": ("casscf",),
}

#: QC capability probes exposed in a diagnostic bundle as booleans only (never
#: the resolved executable path — paths are redacted).
_QC_CAPABILITIES: Final[tuple[str, ...]] = ("orca", "crest", "xtb", "censo", "isostat", "shermo")


# ---------------------------------------------------------------------------
# time / truthiness helpers
# ---------------------------------------------------------------------------


def _iso_utc(epoch: float | None = None) -> str:
    """Return an ISO-8601 UTC timestamp for *epoch* (default: now)."""
    moment = datetime.fromtimestamp(time.time() if epoch is None else epoch, tz=timezone.utc)
    return moment.isoformat()


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Business correlation (persistent) vs trace/span (diagnostic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BusinessContext:
    """Persistent business correlation carried by every structured log line.

    ``job_id`` / ``operation`` survive retries; only ``attempt`` advances.  This
    is an *observation* handle — the durable store/CAS remains the authority for
    settlement and recovery.
    """

    job_id: str | None = None
    attempt: int | None = None
    operation: str | None = None

    def with_attempt(self, attempt: int) -> BusinessContext:
        """Return a copy with a new *attempt*, keeping the persistent identity."""
        return replace(self, attempt=attempt)


@dataclass(frozen=True)
class TraceContext:
    """Diagnostic-only trace/span handle.

    ``trace_id`` is a 32-char lowercase-hex token (survives the redaction net so
    diagnostic identity is preserved); it is deliberately **not** business state
    and never feeds settlement/recovery.  ``retry_of_trace`` links a retry's new
    trace back to the attempt it replaces while ``linked_operation`` preserves
    the existing operation identity.
    """

    trace_id: str
    span_id: str
    operation: str | None = None
    linked_operation: str | None = None
    retry_of_trace: str | None = None


def new_trace(
    operation: str | None = None,
    *,
    retry_of: TraceContext | None = None,
) -> TraceContext:
    """Open a fresh diagnostic trace, optionally linked to a previous one.

    A retry gets a **new** ``trace_id`` (a new diagnostic span tree) but keeps
    the existing operation identity in ``linked_operation`` and records the
    replaced trace in ``retry_of_trace``.
    """
    linked = operation
    if linked is None and retry_of is not None:
        linked = retry_of.linked_operation or retry_of.operation
    return TraceContext(
        trace_id=secrets.token_hex(16),
        span_id=secrets.token_hex(8),
        operation=operation,
        linked_operation=linked,
        retry_of_trace=(retry_of.trace_id if retry_of is not None else None),
    )


# ---------------------------------------------------------------------------
# Structured JSON-line logging
# ---------------------------------------------------------------------------


class JsonLogFormatter(logging.Formatter):
    """Render a log record as a single redacted JSON line.

    The field set is fixed by :data:`LOG_RECORD_KEYS`.  Business correlation may
    arrive either as an ``extra={"business": BusinessContext}`` (preferred) or as
    flat ``extra={"job_id": ..., "attempt": ..., "operation": ...}`` attributes;
    the diagnostic trace arrives as ``extra={"trace": TraceContext}`` or flat
    ``trace_id``/``span_id``.  An ``error_code`` (or an ``Exception``'s ``code``)
    is emitted verbatim — codes are stable and never translated.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 — logging API
        payload: dict[str, Any] = {
            "schema_version": LOG_SCHEMA_VERSION,
            "time": _iso_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "job_id": None,
            "attempt": None,
            "operation": None,
            "error_code": None,
            "trace_id": None,
            "span_id": None,
        }

        business = getattr(record, "business", None)
        if isinstance(business, BusinessContext):
            payload["job_id"] = business.job_id
            payload["attempt"] = business.attempt
            payload["operation"] = business.operation
        for key in ("job_id", "attempt", "operation"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        error_code = getattr(record, "error_code", None)
        if error_code is None:
            error_code = getattr(getattr(record, "error", None), "code", None)
        if error_code is not None:
            payload["error_code"] = str(error_code)

        trace = getattr(record, "trace", None)
        if isinstance(trace, TraceContext):
            payload["trace_id"] = trace.trace_id
            payload["span_id"] = trace.span_id
        for key in ("trace_id", "span_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extra = getattr(record, "structured", None)
        if isinstance(extra, Mapping):
            for key, value in extra.items():
                if str(key) not in ("business", "trace"):
                    payload[str(key)] = value

        # Redact every string value (paths/secrets/URL params); keys are ours.
        return json.dumps(redact_value(payload), ensure_ascii=False, sort_keys=True, default=str)


def structured_logging_enabled() -> bool:
    """Whether ``ACP_LOG_FORMAT=json`` (or ``structured``/``1``) opted in."""
    value = str(os.environ.get(ACP_LOG_FORMAT_ENV, "")).strip().lower()
    return value in {"json", "structured"} or _truthy(value)


def configure_structured_logging(
    *,
    level: str = "INFO",
    stream: Any = None,
    logger: logging.Logger | None = None,
) -> logging.Handler:
    """Install :class:`JsonLogFormatter` on *logger* (default: root).

    Additive and explicit: the caller opts in (CLI ``ACP_LOG_FORMAT=json`` or the
    server hook).  Logs go to ``stderr`` by default so a machine JSON envelope on
    stdout stays clean.  Returns the installed handler.
    """
    numeric_level = getattr(logging, str(level).upper(), logging.INFO)
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(JsonLogFormatter())
    target = logger if logger is not None else logging.getLogger()
    target.handlers[:] = [handler]
    target.setLevel(numeric_level)
    return handler


# ---------------------------------------------------------------------------
# i18n — stable request identity; no enum translation
# ---------------------------------------------------------------------------


def _canonical(value: Any) -> Any:
    if isinstance(value, str):
        # Scientific enum tokens are strings and stay verbatim.
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            if str(key).lower() in _I18N_FIELD_NAMES:
                continue
            out[str(key)] = _canonical(value[key])
        return out
    if isinstance(value, (list, tuple)):
        return [_canonical(entry) for entry in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_canonical(entry) for entry in value), key=str)
    return str(value)


def canonical_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Return the language-independent scientific identity of *request*.

    Drops locale selectors and human-facing text fields (see
    :data:`_I18N_FIELD_NAMES`) but keeps every scientific field and enum token
    verbatim, with deterministic key ordering.
    """
    canonical = _canonical(request)
    return canonical if isinstance(canonical, dict) else {}


def request_hash(request: Mapping[str, Any]) -> str:
    """``"v2:" + sha256(canonical_request)[:32]`` — invariant under zh/en switch."""
    blob = json.dumps(
        canonical_request(request),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return "v2:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def request_plan(request: Mapping[str, Any]) -> dict[str, Any]:
    """Derive the stable plan (workflow + steps + science tokens) from *request*.

    A pure function of :func:`canonical_request`, so an i18n-only change cannot
    move the plan.
    """
    canonical = canonical_request(request)
    workflow = canonical.get("workflow")
    steps = _PLAN_STEPS.get(str(workflow), (str(workflow),)) if workflow is not None else ()
    return {
        "workflow": workflow,
        "steps": list(steps),
        "method": canonical.get("method"),
        "basis": canonical.get("basis"),
        "charge": canonical.get("charge"),
        "multiplicity": canonical.get("multiplicity"),
    }


#: Localised templates for the human message only.  Codes/params are shared.
_ERROR_MESSAGES: Final[dict[str, dict[str, str]]] = {
    "en": {
        "input_invalid": "Invalid input: {field}",
        "backend_unavailable": "Required backend is unavailable: {backend}",
        "runtime_failure": "Execution failed",
    },
    "zh": {
        "input_invalid": "输入无效：{field}",
        "backend_unavailable": "所需后端不可用：{backend}",
        "runtime_failure": "执行失败",
    },
}


def localize_error(
    code: str,
    params: Mapping[str, Any] | None = None,
    *,
    locale: str = DEFAULT_LOCALE,
) -> dict[str, Any]:
    """Return a localised *display* message while the code/params stay frozen.

    The stable ``code`` and the structured ``params`` are identical in every
    locale (only the human ``message`` is translated), so a UI switch cannot
    perturb hash/plan/science identity.
    """
    messages = _ERROR_MESSAGES.get(locale) or _ERROR_MESSAGES[DEFAULT_LOCALE]
    template = (
        messages.get(str(code)) or _ERROR_MESSAGES[DEFAULT_LOCALE].get(str(code)) or str(code)
    )
    safe_params = {str(key): value for key, value in (params or {}).items()}
    try:
        message = template.format(**safe_params)
    except (KeyError, IndexError):
        message = template
    return {"code": str(code), "params": safe_params, "message": message}


# ---------------------------------------------------------------------------
# Optional OpenTelemetry adapter — OFF by default, no resident service
# ---------------------------------------------------------------------------


@runtime_checkable
class OtelAdapter(Protocol):
    """Minimal diagnostic tracing seam (satisfied by a real OTel SDK adapter).

    The service never imports the OTel SDK; a deployment that wants OTel passes
    an adapter to :func:`enable_otel`.  Traces are diagnostic only — they are
    never a settlement/recovery authority.
    """

    enabled: bool

    def start_span(self, name: str, attributes: Mapping[str, Any] | None = None) -> Any: ...


class _NoOpSpan:
    """A span that records nothing and never raises."""

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *exc_info: object) -> Literal[False]:
        return False

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def end(self) -> None:
        return None


class NoOpOtelAdapter:
    """Default adapter: disabled, allocates no exporters, starts no service."""

    enabled = False

    def start_span(self, name: str, attributes: Mapping[str, Any] | None = None) -> _NoOpSpan:
        return _NoOpSpan()


_OTEL_ADAPTER: OtelAdapter = NoOpOtelAdapter()


def otel_enabled() -> bool:
    """Whether OTel export is active.  ``False`` by default (never resident)."""
    return bool(getattr(_OTEL_ADAPTER, "enabled", False))


def otel_settings() -> dict[str, Any]:
    """Report the OTel configuration without activating anything.

    Reads the opt-in/endpoint env vars for diagnostics; the default is off and
    no connection is ever made here.
    """
    return {
        "enabled": otel_enabled(),
        "env_enabled": _truthy(os.environ.get(ACP_OTEL_ENABLED_ENV, "")),
        "endpoint": os.environ.get(ACP_OTEL_ENDPOINT_ENV) or None,
    }


def get_otel_adapter() -> OtelAdapter:
    """Return the active adapter (a :class:`NoOpOtelAdapter` unless enabled)."""
    return _OTEL_ADAPTER


def enable_otel(adapter: OtelAdapter) -> None:
    """Explicitly activate an OTel *adapter* (opt-in; no SDK import here).

    Raises ``ValueError`` if the adapter does not declare ``enabled=True``.
    """
    if not getattr(adapter, "enabled", False):
        raise ValueError("OTel adapter must declare enabled=True")
    global _OTEL_ADAPTER
    _OTEL_ADAPTER = adapter


def disable_otel() -> None:
    """Revert to the default no-op adapter (off)."""
    global _OTEL_ADAPTER
    _OTEL_ADAPTER = NoOpOtelAdapter()


# ---------------------------------------------------------------------------
# System capabilities + diagnostic export
# ---------------------------------------------------------------------------


def system_capabilities(*, probe: Any = None) -> dict[str, Any]:
    """Describe the runtime capabilities for a diagnostic bundle.

    QC binaries are reported as **booleans only** — the resolved executable path
    is host information and is never included (paths are redacted anyway).  Pass
    *probe* (``name -> bool``) to override the default ``shutil.which`` check;
    the default never raises.
    """
    import platform
    import shutil

    resolver = probe if probe is not None else (lambda name: shutil.which(name) is not None)
    capabilities: dict[str, bool] = {}
    for name in _QC_CAPABILITIES:
        capabilities[name] = bool(resolver(name))
    return {
        "python_version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "qc_executables": capabilities,
    }


#: Mapping keys that always carry a secret value → the value is dropped even
#: when it does not match a textual secret pattern (defence in depth for the
#: diagnostic bundle, which may include arbitrary capability/operation records).
_SECRET_KEY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "token",
        "secret",
        "api_key",
        "apikey",
        "access_key",
        "private_key",
        "client_secret",
        "authorization",
        "credential",
        "credentials",
    }
)


def _redact_secret_keys(value: Any) -> Any:
    """Drop the value of any mapping key that names a secret."""
    if isinstance(value, Mapping):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key).lower().replace("-", "_") in _SECRET_KEY_NAMES:
                out[key] = REDACTED_SECRET
            else:
                out[key] = _redact_secret_keys(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact_secret_keys(entry) for entry in value]
    return value


def build_diagnostic_bundle(
    *,
    logs: Sequence[Any] = (),
    capabilities: Mapping[str, Any] | None = None,
    recent_operations: Sequence[Any] = (),
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Assemble a **redacted** diagnostic bundle (logs + capabilities + ops).

    Every string in the bundle passes through the shared machine-output
    redaction net (host paths, secrets, URL query parameters), and any value
    stored under a secret-named key is dropped outright — so a bundle can be
    handed to a support engineer without leaking credentials.
    """
    bundle: dict[str, Any] = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "kind": DIAGNOSTIC_KIND,
        "generated_at": generated_at or _iso_utc(),
        "otel": otel_settings(),
        "logs": [_coerce_json_entry(entry) for entry in logs],
        "capabilities": dict(capabilities) if capabilities is not None else system_capabilities(),
        "recent_operations": [_coerce_json_entry(operation) for operation in recent_operations],
    }
    return dict(redact_value(_redact_secret_keys(bundle)))


def _coerce_json_entry(entry: Any) -> Any:
    """Pass mappings/sequences through; parse JSON strings into objects."""
    if isinstance(entry, str):
        try:
            return json.loads(entry)
        except json.JSONDecodeError:
            return redact(entry)
    return entry


def diagnostic_bundle_json(bundle: Mapping[str, Any], *, indent: int | None = 2) -> str:
    """Serialize a diagnostic bundle with ``ensure_ascii=False``."""
    return json.dumps(dict(bundle), ensure_ascii=False, indent=indent, default=str, sort_keys=True)


def export_diagnostic_bundle(
    destination: Path | str,
    *,
    logs: Sequence[Any] = (),
    capabilities: Mapping[str, Any] | None = None,
    recent_operations: Sequence[Any] = (),
) -> Path:
    """Write a redacted diagnostic bundle to *destination* atomically.

    Uses ``os.replace`` so a reader never sees a partially-written bundle.
    """
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    bundle = build_diagnostic_bundle(
        logs=logs,
        capabilities=capabilities,
        recent_operations=recent_operations,
    )
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(diagnostic_bundle_json(bundle), encoding="utf-8")
    os.replace(temp, target)
    return target
