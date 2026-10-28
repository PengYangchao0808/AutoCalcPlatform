"""Structured logging + diagnostic export gate — B08-6 (todo 47).

Acceptance clauses:

1. **Field schema** — every structured log line is one JSON object with exactly
   :data:`diagnostics.LOG_RECORD_KEYS` (business ``job_id``/``attempt``/
   ``operation`` and stable ``error_code``; diagnostic ``trace_id``/``span_id``).
2. **Redaction by default** — injecting a host path / secret into a log line (or
   a diagnostic bundle) must not leak it; the shared machine-output redaction net
   handles paths, ``key=secret`` assignments, ``Bearer`` tokens and URL params.
3. **i18n invariance** — the same request in zh and en yields the same
   :func:`request_hash` and :func:`request_plan`; scientific enum tokens are never
   translated, and :func:`localize_error` keeps ``code``/``params`` frozen.
4. **OTel off by default** — no adapter activated, no resident service; an
   explicit :func:`enable_otel` opts in and :func:`disable_otel` reverts.
5. **Business vs trace separation** — a retry opens a NEW trace linked to the
   existing operation while the business correlation (job_id/operation) persists.

Plus a static import-direction guard (the module never imports ``acp``) and the
additive CLI / API-server wiring check (legacy text logging preserved by default).
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from acp import cli
from acp_services.entrypoints import diagnostics as d
from acp_services.entrypoints import machine_output as mo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DIAGNOSTICS_PATH = _REPO_ROOT / "src" / "acp_services" / "entrypoints" / "diagnostics.py"

_REQUEST_ZH = {
    "workflow": "singlepoint",
    "method": "r2SCAN-3c",
    "basis": "def2-TZVPPD",
    "charge": 0,
    "multiplicity": 1,
    "locale": "zh",
    "note": "甲醇单点能",
    "message": "输入有效",
}
_REQUEST_EN = {
    "workflow": "singlepoint",
    "method": "r2SCAN-3c",
    "basis": "def2-TZVPPD",
    "charge": 0,
    "multiplicity": 1,
    "locale": "en",
    "note": "methanol single point",
    "message": "input valid",
}


@contextlib.contextmanager
def _log_capture(
    name: str = "acp_services.test.logging",
) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    logger = logging.getLogger(name)
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setFormatter(d.JsonLogFormatter())
    logger.handlers[:] = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger, buffer
    finally:
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate


@contextlib.contextmanager
def _root_logging_guard() -> Iterator[None]:
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def _first_json_line(buffer: io.StringIO) -> dict[str, object]:
    lines = [line for line in buffer.getvalue().splitlines() if line.strip()]
    assert lines, "no log line was emitted"
    return json.loads(lines[0])


# --------------------------------------------------------------------------- #
# 1. Field schema
# --------------------------------------------------------------------------- #


def test_log_line_has_fixed_field_schema() -> None:
    with _log_capture() as (logger, buffer):
        logger.info("plain message")
    payload = _first_json_line(buffer)
    assert set(payload) == set(d.LOG_RECORD_KEYS)
    assert payload["schema_version"] == d.LOG_SCHEMA_VERSION
    assert payload["level"] == "INFO"
    assert payload["logger"] == "acp_services.test.logging"
    assert payload["message"] == "plain message"
    # Correlation fields are present (null) so consumers branch on shape.
    for key in ("job_id", "attempt", "operation", "error_code", "trace_id", "span_id"):
        assert key in payload


def test_business_correlation_and_error_code_populated() -> None:
    business = d.BusinessContext(
        job_id="20261019_001_singlepoint", attempt=2, operation="singlepoint"
    )
    with _log_capture() as (logger, buffer):
        logger.error(
            "backend failed",
            extra={"business": business, "error_code": mo.ErrorCode.BACKEND_UNAVAILABLE},
        )
    payload = _first_json_line(buffer)
    assert payload["job_id"] == "20261019_001_singlepoint"
    assert payload["attempt"] == 2
    assert payload["operation"] == "singlepoint"
    assert payload["error_code"] == mo.ErrorCode.BACKEND_UNAVAILABLE


def test_flat_business_attributes_are_honoured() -> None:
    with _log_capture() as (logger, buffer):
        logger.warning("x", extra={"job_id": "j1", "attempt": 1, "operation": "scan"})
    payload = _first_json_line(buffer)
    assert (payload["job_id"], payload["attempt"], payload["operation"]) == ("j1", 1, "scan")


def test_exception_traceback_is_redacted() -> None:
    try:
        raise RuntimeError("cannot read /home/alice/private/run.log")
    except RuntimeError:
        with _log_capture() as (logger, buffer):
            logger.exception("boom")
    assert "/home/alice" not in buffer.getvalue()
    assert mo.REDACTED_PATH in buffer.getvalue()
    assert "exception" in _first_json_line(buffer)


# --------------------------------------------------------------------------- #
# 2. Redaction by default (log)
# --------------------------------------------------------------------------- #


def test_log_redacts_paths_secrets_and_url_params() -> None:
    message = (
        "read /home/alice/secret/run.log failed; token=abcDEF123456; "
        "Bearer eyJhbGciOi.zzz; https://user:pw@host/a?token=deadbeef&x=1"
    )
    with _log_capture() as (logger, buffer):
        logger.info(message)
    raw = buffer.getvalue()
    for leaked in ("/home/alice", "abcDEF123456", "eyJhbGciOi", "user:pw", "deadbeef"):
        assert leaked not in raw, f"leaked {leaked!r}: {raw}"
    assert mo.REDACTED_PATH in raw
    assert mo.REDACTED_SECRET in raw


def test_redaction_gate_has_teeth() -> None:
    """A redaction removal (identity formatter) would leak the secret."""
    record = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=supersecretvalue",
        args=(),
        exc_info=None,
    )
    formatted = d.JsonLogFormatter().format(record)
    assert "supersecretvalue" not in formatted
    assert mo.REDACTED_SECRET in formatted


# --------------------------------------------------------------------------- #
# 3. Business vs trace separation (retry links, does not rewrite business)
# --------------------------------------------------------------------------- #


def test_business_context_persists_across_retry() -> None:
    first = d.BusinessContext(job_id="j1", attempt=1, operation="singlepoint")
    retried = first.with_attempt(2)
    assert retried.job_id == first.job_id
    assert retried.operation == first.operation
    assert retried.attempt == 2


def test_retry_opens_new_trace_linked_to_operation() -> None:
    first = d.new_trace("singlepoint")
    retried = d.new_trace("singlepoint", retry_of=first)
    assert retried.trace_id != first.trace_id
    assert retried.retry_of_trace == first.trace_id
    assert retried.linked_operation == "singlepoint"


def test_trace_identity_is_emitted_and_survives_redaction() -> None:
    trace = d.new_trace("singlepoint")
    with _log_capture() as (logger, buffer):
        logger.info("x", extra={"trace": trace})
    payload = _first_json_line(buffer)
    assert payload["trace_id"] == trace.trace_id
    assert payload["span_id"] == trace.span_id


# --------------------------------------------------------------------------- #
# 4. i18n invariance — same request zh/en => same hash + plan
# --------------------------------------------------------------------------- #


def test_request_hash_and_plan_invariant_under_locale_switch() -> None:
    assert d.request_hash(_REQUEST_ZH) == d.request_hash(_REQUEST_EN)
    assert d.request_plan(_REQUEST_ZH) == d.request_plan(_REQUEST_EN)


def test_canonical_request_drops_text_but_keeps_enum_tokens() -> None:
    canonical = d.canonical_request(_REQUEST_ZH)
    assert canonical["method"] == "r2SCAN-3c"
    assert canonical["basis"] == "def2-TZVPPD"
    assert canonical["workflow"] == "singlepoint"
    assert "locale" not in canonical
    assert "note" not in canonical
    assert "message" not in canonical


def test_localize_error_keeps_code_and_params_stable() -> None:
    params = {"field": "charge"}
    en = d.localize_error(mo.ErrorCode.INPUT_INVALID, params, locale="en")
    zh = d.localize_error(mo.ErrorCode.INPUT_INVALID, params, locale="zh")
    assert en["code"] == zh["code"] == mo.ErrorCode.INPUT_INVALID
    assert en["params"] == zh["params"] == params
    assert en["message"] != zh["message"]  # only the display text differs


def test_unknown_locale_falls_back_to_default() -> None:
    unknown = d.localize_error(mo.ErrorCode.RUNTIME_FAILURE, locale="fr")
    default = d.localize_error(mo.ErrorCode.RUNTIME_FAILURE, locale="en")
    assert unknown["code"] == default["code"]
    assert unknown["message"] == default["message"]


# --------------------------------------------------------------------------- #
# 5. Optional OTel — OFF by default, no resident service
# --------------------------------------------------------------------------- #


def test_otel_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(d.ACP_OTEL_ENABLED_ENV, raising=False)
    d.disable_otel()
    assert d.otel_enabled() is False
    assert isinstance(d.get_otel_adapter(), d.NoOpOtelAdapter)
    assert d.otel_settings()["enabled"] is False
    # The no-op span is inert and never raises.
    with d.get_otel_adapter().start_span("x", {"k": "v"}) as span:
        span.set_attribute("a", 1)
        span.end()


def test_otel_env_does_not_auto_activate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(d.ACP_OTEL_ENABLED_ENV, "1")
    d.disable_otel()
    assert d.otel_settings()["env_enabled"] is True
    assert d.otel_enabled() is False  # env is advisory; activation is explicit


def test_otel_enable_requires_opt_in_adapter() -> None:
    d.disable_otel()

    class _FakeAdapter:
        enabled = True
        spans: list[str]

        def __init__(self) -> None:
            self.spans = []

        def start_span(self, name: str, attributes: object = None) -> object:
            self.spans.append(name)
            return d.NoOpOtelAdapter().start_span(name)

    adapter = _FakeAdapter()
    try:
        d.enable_otel(adapter)
        assert d.otel_enabled() is True
        assert d.get_otel_adapter() is adapter
    finally:
        d.disable_otel()
    assert d.otel_enabled() is False

    class _Disabled:
        enabled = False

        def start_span(self, name: str, attributes: object = None) -> object:
            return d.NoOpOtelAdapter().start_span(name)

    with pytest.raises(ValueError):
        d.enable_otel(_Disabled())  # type: ignore[arg-type]


def test_module_starts_no_resident_service() -> None:
    source = _DIAGNOSTICS_PATH.read_text(encoding="utf-8")
    for forbidden in ("start_http_server", "Thread(", "atexit", "socketserver"):
        assert forbidden not in source, (
            f"diagnostics must not start a resident service: {forbidden}"
        )


# --------------------------------------------------------------------------- #
# 6. Diagnostic bundle — schema + redaction
# --------------------------------------------------------------------------- #


def test_diagnostic_bundle_schema_and_redaction() -> None:
    bundle = d.build_diagnostic_bundle(
        logs=[
            "reading /home/alice/secret/run.log",
            '{"msg": "failed", "token": "abcDEF123456"}',
        ],
        capabilities={"api_key": "hunter2", "qc_executables": {"orca": True}},
        recent_operations=[{"path": "/etc/shadow", "operation": "singlepoint"}],
    )
    assert set(bundle) == {
        "schema_version",
        "kind",
        "generated_at",
        "otel",
        "logs",
        "capabilities",
        "recent_operations",
    }
    assert bundle["kind"] == "acp_services_diagnostics"
    blob = d.diagnostic_bundle_json(bundle)
    for leaked in ("/home/alice", "/etc/shadow", "abcDEF123456", "hunter2"):
        assert leaked not in blob, f"bundle leaked {leaked!r}: {blob}"
    assert bundle["capabilities"]["api_key"] == mo.REDACTED_SECRET
    assert bundle["otel"]["enabled"] is False


def test_diagnostic_bundle_uses_system_capabilities_by_default() -> None:
    bundle = d.build_diagnostic_bundle()
    assert "python_version" in bundle["capabilities"]
    assert set(bundle["capabilities"]["qc_executables"]) == set(
        ("orca", "crest", "xtb", "censo", "isostat", "shermo")
    )
    # Boolean availability only — the resolved path (host info) is never included.
    assert all(isinstance(v, bool) for v in bundle["capabilities"]["qc_executables"].values())


def test_export_diagnostic_bundle_is_redacted_and_atomic(tmp_path: Path) -> None:
    destination = tmp_path / "diag" / "bundle.json"
    written = d.export_diagnostic_bundle(
        destination,
        logs=[{"message": "token=abcDEF123456 at /home/alice/x"}],
    )
    assert written == destination and destination.is_file()
    text = destination.read_text(encoding="utf-8")
    assert "abcDEF123456" not in text and "/home/alice" not in text
    assert not list(destination.parent.glob("*.tmp"))
    assert json.loads(text)["kind"] == "acp_services_diagnostics"


# --------------------------------------------------------------------------- #
# 7. Static import direction guard
# --------------------------------------------------------------------------- #


def test_diagnostics_module_imports_no_acp() -> None:
    tree = ast.parse(_DIAGNOSTICS_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not (a.name == "acp" or a.name.startswith("acp.")) for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not (node.module == "acp" or node.module.startswith("acp."))


# --------------------------------------------------------------------------- #
# 8. Additive wiring — CLI + API server (legacy text preserved by default)
# --------------------------------------------------------------------------- #


def test_structured_logging_enabled_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(d.ACP_LOG_FORMAT_ENV, raising=False)
    assert d.structured_logging_enabled() is False
    for value in ("json", "structured", "1", "true", "on"):
        monkeypatch.setenv(d.ACP_LOG_FORMAT_ENV, value)
        assert d.structured_logging_enabled() is True
    monkeypatch.setenv(d.ACP_LOG_FORMAT_ENV, "plain")
    assert d.structured_logging_enabled() is False


def test_cli_setup_logging_default_is_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(d.ACP_LOG_FORMAT_ENV, raising=False)
    with _root_logging_guard():
        cli.setup_logging("INFO")
        root = logging.getLogger()
        assert not any(isinstance(h.formatter, d.JsonLogFormatter) for h in root.handlers)


def test_cli_setup_logging_structured_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(d.ACP_LOG_FORMAT_ENV, "json")
    with _root_logging_guard():
        cli.setup_logging("INFO")
        root = logging.getLogger()
        assert any(isinstance(h.formatter, d.JsonLogFormatter) for h in root.handlers)


def test_server_logging_hook_is_inert_by_default_and_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import acp.api.server as server

    with _root_logging_guard():
        monkeypatch.delenv(d.ACP_LOG_FORMAT_ENV, raising=False)
        assert server._maybe_configure_structured_logging() is False
        monkeypatch.setenv(d.ACP_LOG_FORMAT_ENV, "json")
        assert server._maybe_configure_structured_logging() is True
        assert isinstance(logging.getLogger().handlers[0].formatter, d.JsonLogFormatter)
