#!/usr/bin/env python3
"""ACP 2.0 model budget proxy (T-13a / C09).

An OpenAI-compatible Chat Completions proxy that sits between DSH and the
approved model provider.  It enforces the frozen per-scenario budget **before**
a request leaves the machine:

* ``model_requests`` <= 12, ``input_tokens`` <= 150k, ``output_tokens`` <= 30k
  for one ``scenario_budget_id``;
* one in-flight request per scenario (concurrency 1);
* one per-request output cap (``PER_REQUEST_OUTPUT_CAP``); a client that asks
  for more is refused, never clamped;
* the wire ``max_tokens`` is clamped to the *remaining* cumulative output so a
  well-behaved upstream can never make the settled total exceed the cap.

Budget identity is independent of ``run_id``.  The ledger
(``$ACP2_DATA_ROOT/budget/ledger.json``) carries one ``batch_id`` for the whole
acceptance batch and one entry per ``scenario_budget_id``; retries, proxy
restarts and new run IDs all inherit the remaining allowance.  Nothing in this
module keys accounting off a run ID.

The proxy is model-provider only: it never talks to the ACP Controller, never
registers an MCP transport, and never launches a scientific process.

This file is stdlib-only.  ``selftest`` drives the whole boundary matrix
against a local controlled upstream stub and never spends a real model request.

Exit codes: ``0`` PASS, ``1`` FAIL, ``2`` NOT_VERIFIED / refused start,
``3`` usage error.
"""

from __future__ import annotations

import argparse
import fcntl
import http.client
import ipaddress
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LEDGER_SCHEMA = "acp2.model-budget.ledger.v1"
SELFTEST_SCHEMA = "acp2.model-budget.selftest.v1"
STATE_SCHEMA = "acp2.model-budget.proxy-state.v1"
DEFAULT_CAPS: dict[str, int] = {
    "model_requests": 12,
    "input_tokens": 150_000,
    "output_tokens": 30_000,
}
DEFAULT_SCENARIO_BUDGET_ID = "model-live"
# One request may never ask for more output than this, whatever the remaining
# cumulative allowance is.
PER_REQUEST_OUTPUT_CAP = 4096
# A token estimate that is deliberately conservative (ceil(chars/3)) and is
# always labelled "estimated"; a reported usage always wins.
ESTIMATE_CHARS_PER_TOKEN = 3
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 500
UPSTREAM_TIMEOUT = 120.0
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
SELFTEST_MODELS = (
    "acp2-proxy-model",
    "stub-ok",
    "stub-usage10",
    "stub-no-usage",
    "stub-error",
    "stub-slow",
    "stub-interrupt",
)
SECRET_SCAN_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{8,}|sk-[a-z0-9]{8,}|api[_-]?key\s*[:=]\s*[^\s]+)"
)

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_NOT_VERIFIED = 2
EXIT_USAGE = 3


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _data_root(value: str | None) -> Path:
    raw = value or os.environ.get("ACP2_DATA_ROOT") or ""
    if not raw:
        raise ValueError("no data root: pass --data-root or set ACP2_DATA_ROOT")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        raise ValueError(f"data root must be absolute: {root}")
    return root


def ledger_path(data_root: Path) -> Path:
    return data_root / "budget" / "ledger.json"


def default_log_path(data_root: Path) -> Path:
    return data_root / "budget" / "proxy.jsonl"


@contextmanager
def _ledger_lock(data_root: Path):
    """Cross-process read-modify-write lock for the single JSON ledger."""

    lock_path = data_root / "budget" / "ledger.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _load_ledger(data_root: Path) -> dict[str, Any]:
    path = ledger_path(data_root)
    if not path.is_file():
        return {
            "schema": LEDGER_SCHEMA,
            "batch_id": f"batch-{uuid.uuid4().hex[:16]}",
            "created_at": _now(),
            "updated_at": _now(),
            "scenarios": {},
        }
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable ledger {path}: {exc}") from exc
    if not isinstance(document, dict) or document.get("schema") != LEDGER_SCHEMA:
        raise ValueError(f"ledger schema mismatch in {path}")
    if not isinstance(document.get("scenarios"), dict) or not document.get("batch_id"):
        raise ValueError(f"ledger missing batch_id/scenarios: {path}")
    return document


def _save_ledger(data_root: Path, document: dict[str, Any]) -> None:
    document["updated_at"] = _now()
    _atomic_write(ledger_path(data_root), json.dumps(document, indent=2, sort_keys=True) + "\n")


def estimate_input_tokens(payload: dict[str, Any]) -> int:
    """Conservative pre-request estimate; never presented as measured usage."""

    try:
        text = json.dumps(payload.get("messages") or [], ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = ""
    return max(1, (len(text) + ESTIMATE_CHARS_PER_TOKEN - 1) // ESTIMATE_CHARS_PER_TOKEN + 32)


def _caps_from_args(args: argparse.Namespace) -> dict[str, int] | None:
    provided = {
        "model_requests": args.max_requests,
        "input_tokens": args.input_token_cap,
        "output_tokens": args.output_token_cap,
    }
    if all(value is None for value in provided.values()):
        return None
    merged = dict(DEFAULT_CAPS)
    for key, value in provided.items():
        if value is not None:
            if value <= 0:
                raise ValueError(f"{key} cap must be positive")
            merged[key] = int(value)
    return merged


def _normalize_base_url(value: str) -> urllib.parse.SplitResult:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"upstream base URL must be http(s)://host[:port][/path]: {value}")
    return parsed


# ---------------------------------------------------------------------------
# Ledger operations
# ---------------------------------------------------------------------------


class CapsMismatchError(RuntimeError):
    """A restart tried to change an existing scenario's frozen caps."""


class BudgetRefusalError(RuntimeError):
    """A request was refused before it reached the provider."""

    def __init__(self, code: str, message: str, status: int = 429) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def body(self, batch_id: str, budget_id: str) -> dict[str, Any]:
        return {
            "error": {
                "type": "acp2_budget_refusal",
                "code": self.code,
                "message": self.message,
                "batch_id": batch_id,
                "scenario_budget_id": budget_id,
            }
        }


@dataclass(frozen=True)
class Reservation:
    rid: str
    budget_id: str
    run_id: str
    model: str
    stream: bool
    estimated_input: int
    requested_max_tokens: int | None
    sent_max_tokens: int
    reserved_at: str


def _new_scenario(budget_id: str, caps: dict[str, int], models: list[str]) -> dict[str, Any]:
    return {
        "scenario_budget_id": budget_id,
        "caps": dict(caps),
        "models": sorted(set(models)),
        "settled": {
            "requests": 0,
            "input_tokens_reported": 0,
            "input_tokens_estimated": 0,
            "output_tokens_reported": 0,
            "output_tokens_unknown_events": 0,
            "unknown_usage_events": 0,
            "input_cap_overshoot_events": 0,
            "output_cap_violation_events": 0,
        },
        "inflight": {"requests": 0, "input_tokens": 0},
        "refused": {},
        "run_ids": [],
        "events": [],
        "events_truncated": 0,
        "seq": 0,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _append_event(scenario: dict[str, Any], event: dict[str, Any]) -> None:
    scenario["seq"] = int(scenario.get("seq", 0)) + 1
    event["seq"] = scenario["seq"]
    event["ts"] = _now()
    events = scenario.setdefault("events", [])
    events.append(event)
    if len(events) > MAX_EVENTS:
        del events[: len(events) - MAX_EVENTS]
        scenario["events_truncated"] = int(scenario.get("events_truncated", 0)) + 1
    scenario["updated_at"] = event["ts"]


def _ensure_scenario(
    document: dict[str, Any],
    budget_id: str,
    caps: dict[str, int] | None,
    models: list[str],
) -> dict[str, Any]:
    scenarios = document["scenarios"]
    existing = scenarios.get(budget_id)
    if existing is None:
        scenario = _new_scenario(budget_id, caps or dict(DEFAULT_CAPS), models)
        scenarios[budget_id] = scenario
        return scenario
    if caps is not None and dict(existing.get("caps") or {}) != dict(caps):
        raise CapsMismatchError(
            f"scenario {budget_id!r} already has caps {existing.get('caps')}; "
            f"refusing to change them to {caps} (no silent widening or narrowing)"
        )
    stored_models = list(existing.get("models") or [])
    existing["models"] = sorted(set(stored_models) | set(models))
    return existing


def health_snapshot(document: dict[str, Any], budget_id: str, data_root: Path) -> dict[str, Any]:
    scenario = document["scenarios"].get(budget_id)
    if scenario is None:
        raise KeyError(budget_id)
    caps = dict(scenario["caps"])
    settled = scenario["settled"]
    inflight = scenario["inflight"]
    input_used = int(settled["input_tokens_reported"]) + int(settled["input_tokens_estimated"])
    output_used = int(settled["output_tokens_reported"])
    usage = {
        "model_calls": int(settled["requests"]),
        "input_tokens": input_used,
        "input_tokens_reported": int(settled["input_tokens_reported"]),
        "input_tokens_estimated": int(settled["input_tokens_estimated"]),
        "output_tokens": output_used,
        "output_tokens_reported": output_used,
        "output_tokens_unknown_events": int(settled["output_tokens_unknown_events"]),
        "unknown_usage_events": int(settled["unknown_usage_events"]),
        "total_tokens": input_used + output_used,
        "total_tokens_complete": int(settled["unknown_usage_events"]) == 0,
        "input_cap_overshoot_events": int(settled["input_cap_overshoot_events"]),
        "output_cap_violation_events": int(settled["output_cap_violation_events"]),
    }
    remaining = {
        "model_requests": caps["model_requests"]
        - int(settled["requests"])
        - int(inflight["requests"]),
        "input_tokens": caps["input_tokens"] - input_used - int(inflight["input_tokens"]),
        "output_tokens": caps["output_tokens"] - output_used,
    }
    return {
        "schema": LEDGER_SCHEMA,
        "status": "ok",
        "batch_id": document["batch_id"],
        "scenario_budget_id": budget_id,
        "caps": caps,
        "models": list(scenario.get("models") or []),
        "usage": usage,
        "remaining": remaining,
        "inflight": dict(inflight),
        "refused": dict(scenario.get("refused") or {}),
        "run_ids": list(scenario.get("run_ids") or []),
        "events_tail": list(scenario.get("events") or [])[-5:],
        "ledger_path": str(ledger_path(data_root)),
        "updated_at": document.get("updated_at"),
    }


class BudgetStore:
    """Serialized ledger access; every mutation is one locked load/save cycle."""

    def __init__(
        self,
        data_root: Path,
        budget_id: str,
        caps: dict[str, int] | None,
        models: list[str],
    ) -> None:
        self.data_root = data_root
        self.budget_id = budget_id
        self.caps = caps
        self.models = models

    def ensure(self) -> dict[str, Any]:
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            _ensure_scenario(document, self.budget_id, self.caps, self.models)
            _save_ledger(self.data_root, document)
            return document

    def recover_inflight(self) -> int:
        """Charge an in-flight request left by a crashed proxy, then clear it.

        A crash must not refund a request: the provider may have billed it and
        its usage is unknowable.  The recovery is recorded as ``restart_recovered``
        with an estimated input and unknown output.
        """

        recovered = 0
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            scenario = _ensure_scenario(document, self.budget_id, self.caps, self.models)
            inflight = scenario["inflight"]
            if int(inflight.get("requests") or 0) > 0:
                recovered = int(inflight["requests"])
                settled = scenario["settled"]
                settled["requests"] += recovered
                settled["input_tokens_estimated"] += int(inflight.get("input_tokens") or 0)
                settled["unknown_usage_events"] += recovered
                settled["output_tokens_unknown_events"] += recovered
                _append_event(
                    scenario,
                    {
                        "kind": "request",
                        "counted": True,
                        "outcome": "restart_recovered",
                        "run_id": None,
                        "model": None,
                        "stream": False,
                        "usage": {
                            "input_tokens": None,
                            "output_tokens": None,
                            "source": "unknown",
                        },
                        "detail": {
                            "recovered_requests": recovered,
                            "input_tokens_estimate": int(inflight.get("input_tokens") or 0),
                        },
                    },
                )
                inflight["requests"] = 0
                inflight["input_tokens"] = 0
                _save_ledger(self.data_root, document)
        return recovered

    def reserve(
        self,
        *,
        run_id: str,
        model: str,
        stream: bool,
        estimated_input: int,
        requested_max_tokens: int | None,
    ) -> Reservation:
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            scenario = _ensure_scenario(document, self.budget_id, self.caps, self.models)
            caps = scenario["caps"]
            settled = scenario["settled"]
            inflight = scenario["inflight"]

            if int(inflight["requests"]) >= 1:
                _refuse_locked(
                    self.data_root,
                    document,
                    scenario,
                    model=model,
                    run_id=run_id,
                    stream=stream,
                    code="concurrency_cap",
                    message="only one model request may be in flight per scenario",
                    status=429,
                )
            used_requests = int(settled["requests"]) + int(inflight["requests"])
            if used_requests + 1 > int(caps["model_requests"]):
                _refuse_locked(
                    self.data_root,
                    document,
                    scenario,
                    model=model,
                    run_id=run_id,
                    stream=stream,
                    code="model_requests",
                    message=(
                        f"model request cap reached ({caps['model_requests']}); "
                        "the next request is refused before reaching the provider"
                    ),
                    status=429,
                )
            used_input = (
                int(settled["input_tokens_reported"])
                + int(settled["input_tokens_estimated"])
                + int(inflight["input_tokens"])
            )
            if used_input + estimated_input > int(caps["input_tokens"]):
                _refuse_locked(
                    self.data_root,
                    document,
                    scenario,
                    model=model,
                    run_id=run_id,
                    stream=stream,
                    code="input_tokens",
                    message=(
                        f"input token cap would be exceeded: used={used_input} "
                        f"estimate={estimated_input} cap={caps['input_tokens']}"
                    ),
                    status=429,
                )
            remaining_output = int(caps["output_tokens"]) - int(settled["output_tokens_reported"])
            if remaining_output <= 0:
                _refuse_locked(
                    self.data_root,
                    document,
                    scenario,
                    model=model,
                    run_id=run_id,
                    stream=stream,
                    code="output_tokens",
                    message=(
                        f"output token cap reached ({caps['output_tokens']}); "
                        "no further model request may run"
                    ),
                    status=429,
                )
            if requested_max_tokens is not None and requested_max_tokens > PER_REQUEST_OUTPUT_CAP:
                _refuse_locked(
                    self.data_root,
                    document,
                    scenario,
                    model=model,
                    run_id=run_id,
                    stream=stream,
                    code="per_request_output_cap",
                    message=(
                        f"requested max_tokens={requested_max_tokens} exceeds the "
                        f"per-request output cap {PER_REQUEST_OUTPUT_CAP}"
                    ),
                    status=400,
                )
            wire_max = min(
                requested_max_tokens
                if requested_max_tokens is not None
                else PER_REQUEST_OUTPUT_CAP,
                PER_REQUEST_OUTPUT_CAP,
                remaining_output,
            )
            reservation = Reservation(
                rid=uuid.uuid4().hex,
                budget_id=self.budget_id,
                run_id=run_id,
                model=model,
                stream=stream,
                estimated_input=estimated_input,
                requested_max_tokens=requested_max_tokens,
                sent_max_tokens=wire_max,
                reserved_at=_now(),
            )
            inflight["requests"] = int(inflight["requests"]) + 1
            inflight["input_tokens"] = int(inflight["input_tokens"]) + estimated_input
            run_ids = scenario.setdefault("run_ids", [])
            if run_id and run_id not in run_ids:
                run_ids.append(run_id)
            _save_ledger(self.data_root, document)
            return reservation

    def record_refusal(
        self,
        *,
        code: str,
        message: str,
        model: str | None = None,
        run_id: str | None = None,
        stream: bool = False,
        detail: Any = None,
    ) -> None:
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            scenario = _ensure_scenario(document, self.budget_id, self.caps, self.models)
            refused = scenario.setdefault("refused", {})
            refused[code] = int(refused.get(code, 0)) + 1
            _append_event(
                scenario,
                {
                    "kind": "refusal",
                    "counted": False,
                    "outcome": code,
                    "run_id": run_id,
                    "model": model,
                    "stream": stream,
                    "detail": detail if detail is not None else message,
                },
            )
            _save_ledger(self.data_root, document)

    def settle(
        self,
        reservation: Reservation,
        *,
        outcome: str,
        usage: tuple[int | None, int | None] | None,
        detail: Any = None,
    ) -> dict[str, Any]:
        """Settle one forwarded request exactly once.

        ``completed`` (with or without usage), ``client_disconnected``,
        ``upstream_interrupted``, ``upstream_error`` and ``upstream_status_N``
        all consume one request: the attempt reached the provider.  A missing
        usage is recorded as unknown (estimated for input, null for output) and
        never fabricated.
        """

        input_tokens = usage[0] if usage else None
        output_tokens = usage[1] if usage else None
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            scenario = _ensure_scenario(document, self.budget_id, self.caps, self.models)
            inflight = scenario["inflight"]
            settled = scenario["settled"]
            inflight["requests"] = max(0, int(inflight["requests"]) - 1)
            inflight["input_tokens"] = max(
                0, int(inflight["input_tokens"]) - reservation.estimated_input
            )
            settled["requests"] += 1

            input_source = "reported"
            if input_tokens is None:
                input_source = "estimated"
                settled["input_tokens_estimated"] += reservation.estimated_input
                settled["unknown_usage_events"] += 1
                if int(settled["input_tokens_reported"]) + int(
                    settled["input_tokens_estimated"]
                ) > int(scenario["caps"]["input_tokens"]):
                    settled["input_cap_overshoot_events"] += 1
            else:
                settled["input_tokens_reported"] += int(input_tokens)
                if int(input_tokens) > reservation.estimated_input:
                    # The pre-request estimate under-counted the real prompt.
                    # Record the delta honestly; the cap check remains the
                    # pre-request estimate documented in model-budget.md.
                    if int(settled["input_tokens_reported"]) + int(
                        settled["input_tokens_estimated"]
                    ) > int(scenario["caps"]["input_tokens"]):
                        settled["input_cap_overshoot_events"] += 1

            output_source = "reported"
            if output_tokens is None:
                output_source = "unknown"
                settled["output_tokens_unknown_events"] += 1
                if input_tokens is not None:
                    # input was reported, output was not: still an unknown event
                    pass
            else:
                settled["output_tokens_reported"] += int(output_tokens)
                if int(output_tokens) > reservation.sent_max_tokens:
                    settled["output_cap_violation_events"] += 1

            _append_event(
                scenario,
                {
                    "kind": "request",
                    "counted": True,
                    "outcome": outcome,
                    "run_id": reservation.run_id,
                    "model": reservation.model,
                    "stream": reservation.stream,
                    "requested_max_tokens": reservation.requested_max_tokens,
                    "sent_max_tokens": reservation.sent_max_tokens,
                    "estimated_input_tokens": reservation.estimated_input,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "input_source": input_source,
                        "output_source": output_source,
                    },
                    "detail": detail,
                },
            )
            _save_ledger(self.data_root, document)
            return health_snapshot(document, self.budget_id, self.data_root)

    def snapshot(self) -> dict[str, Any]:
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            _ensure_scenario(document, self.budget_id, self.caps, self.models)
            return health_snapshot(document, self.budget_id, self.data_root)

    def document(self) -> dict[str, Any]:
        with _ledger_lock(self.data_root):
            return _load_ledger(self.data_root)


# Fix _refuse_locked's ledger write: it needs the store's data root.  Defined as
# a separate helper so the refusal path stays a single locked cycle.
def _refuse_locked(
    data_root: Path,
    document: dict[str, Any],
    scenario: dict[str, Any],
    *,
    model: str | None,
    run_id: str | None,
    stream: bool,
    code: str,
    message: str,
    status: int,
) -> None:
    refused = scenario.setdefault("refused", {})
    refused[code] = int(refused.get(code, 0)) + 1
    _append_event(
        scenario,
        {
            "kind": "refusal",
            "counted": False,
            "outcome": code,
            "run_id": run_id,
            "model": model,
            "stream": stream,
            "detail": message,
        },
    )
    _save_ledger(data_root, document)
    raise BudgetRefusalError(code, message, status)


# ---------------------------------------------------------------------------
# Usage extraction
# ---------------------------------------------------------------------------


def _usage_pair(container: Any) -> tuple[int | None, int | None] | None:
    if not isinstance(container, dict):
        return None
    usage = container.get("usage")
    if not isinstance(usage, dict):
        return None
    raw_in = usage.get("prompt_tokens", usage.get("input_tokens"))
    raw_out = usage.get("completion_tokens", usage.get("output_tokens"))
    inp = int(raw_in) if isinstance(raw_in, (int, float)) else None
    out = int(raw_out) if isinstance(raw_out, (int, float)) else None
    if inp is None and out is None:
        return None
    return (inp, out)


def _usage_from_json_bytes(raw: bytes) -> tuple[int | None, int | None] | None:
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _usage_pair(document)


class SseUsageCollector:
    """Incrementally scan an SSE byte stream for a usage-bearing final chunk."""

    def __init__(self) -> None:
        self._buffer = b""
        self.usage: tuple[int | None, int | None] | None = None
        self.saw_done = False
        self.saw_data = False
        self.saw_error_frame = False

    def feed(self, chunk: bytes) -> None:
        self._buffer += chunk
        while b"\n" in self._buffer:
            line, _, self._buffer = self._buffer.partition(b"\n")
            line = line.strip()
            if not line.startswith(b"data:"):
                if line.startswith(b"event:") and b"error" in line.lower():
                    self.saw_error_frame = True
                continue
            data = line[5:].strip()
            if data == b"[DONE]":
                self.saw_done = True
                continue
            try:
                payload = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            self.saw_data = True
            pair = _usage_pair(payload)
            if pair is not None:
                previous = self.usage or (None, None)
                merged = (
                    pair[0] if pair[0] is not None else previous[0],
                    pair[1] if pair[1] is not None else previous[1],
                )
                self.usage = merged


# ---------------------------------------------------------------------------
# HTTP proxy
# ---------------------------------------------------------------------------


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        store: BudgetStore,
        upstream: urllib.parse.SplitResult,
        model_ids: list[str],
        log_path: Path,
        state_path: Path | None,
        started_at: str,
    ) -> None:
        super().__init__(address, handler)
        self.store = store
        self.upstream = upstream
        self.model_ids = list(model_ids)
        self.log_path = log_path
        self.state_path = state_path
        self.started_at = started_at
        self.stop_event = threading.Event()
        self._log_lock = threading.Lock()

    def log_event(self, event: dict[str, Any]) -> None:
        record = {"ts": _now(), "event": event}
        line = json.dumps(record, sort_keys=True) + "\n"
        with self._log_lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ACP2BudgetProxy/1.0"

    # -- plumbing ---------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003 - stdlib hook
        # The JSONL log owns the record; stderr stays quiet.
        return

    @property
    def proxy(self) -> ProxyServer:
        return self.server  # type: ignore[return-value]

    def _send_json(
        self,
        status: int,
        document: dict[str, Any],
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(document, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError(f"invalid Content-Length: {raw_length}") from exc
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError(f"request body length out of bounds: {length}")
        raw = self.rfile.read(length)
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise ValueError("request body must be a JSON object")
        return document

    # -- GET --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path in ("/health", "/__health"):
            try:
                snapshot = self.proxy.store.snapshot()
            except (OSError, ValueError, KeyError) as exc:
                self._send_json(503, {"status": "error", "detail": str(exc)})
                return
            snapshot["pid"] = os.getpid()
            snapshot["upstream"] = f"{self.proxy.upstream.scheme}://{self.proxy.upstream.netloc}"
            snapshot["log_path"] = str(self.proxy.log_path)
            self._send_json(200, snapshot)
            return
        if path in ("/ready", "/__ready"):
            self._send_json(
                200,
                {
                    "ready": True,
                    "batch_id": self.proxy.store.snapshot()["batch_id"],
                    "upstream_configured": bool(self.proxy.upstream.hostname),
                },
            )
            return
        if path in ("/v1/models", "/models"):
            models = [
                {"id": model, "object": "model", "owned_by": "acp2-budget-proxy"}
                for model in (self.proxy.model_ids or ["acp2-proxy-model"])
            ]
            self._send_json(200, {"object": "list", "data": models})
            return
        self._send_json(404, {"error": {"type": "acp2_proxy_not_found", "message": path}})

    # -- POST -------------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook
        path = urllib.parse.urlsplit(self.path).path.rstrip("/") or "/"
        if path in ("/__stop", "/stop"):
            self._send_json(200, {"stopping": True, "pid": os.getpid()})
            threading.Thread(target=self._shutdown, daemon=True).start()
            return
        if path in ("/v1/chat/completions", "/chat/completions"):
            self._chat_completions()
            return
        self._send_json(404, {"error": {"type": "acp2_proxy_not_found", "message": path}})

    def _shutdown(self) -> None:
        time.sleep(0.1)
        self.proxy.shutdown()

    def _chat_completions(self) -> None:
        try:
            payload = self._read_json()
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(
                400,
                {"error": {"type": "acp2_proxy_bad_request", "message": str(exc)}},
            )
            return

        model = payload.get("model")
        if not isinstance(model, str) or not model:
            self._send_json(
                400,
                {"error": {"type": "acp2_proxy_bad_request", "message": "model is required"}},
            )
            return
        run_id = self.headers.get("X-ACP2-Run-Id") or ""
        stream = bool(payload.get("stream"))
        raw_max = payload.get("max_tokens", payload.get("max_completion_tokens"))
        requested_max: int | None
        if raw_max is None:
            requested_max = None
        elif isinstance(raw_max, int) and not isinstance(raw_max, bool):
            if raw_max <= 0:
                self._send_json(
                    400,
                    {
                        "error": {
                            "type": "acp2_proxy_bad_request",
                            "message": f"max_tokens must be positive: {raw_max}",
                        }
                    },
                )
                return
            requested_max = raw_max
        else:
            self._send_json(
                400,
                {
                    "error": {
                        "type": "acp2_proxy_bad_request",
                        "message": f"max_tokens must be an integer: {raw_max!r}",
                    }
                },
            )
            return

        if self.proxy.model_ids and model not in self.proxy.model_ids:
            store = self.proxy.store
            snapshot = store.snapshot()
            store.record_refusal(
                code="unapproved_model",
                message=f"model {model!r} is not in the approved route list",
                model=model,
                run_id=run_id,
                stream=stream,
                detail={
                    "requested_model": model,
                    "approved_models": list(self.proxy.model_ids),
                    "fallback_attempted": False,
                },
            )
            self.proxy.log_event(
                {
                    "kind": "refusal",
                    "code": "unapproved_model",
                    "model": model,
                    "run_id": run_id,
                    "batch_id": snapshot["batch_id"],
                }
            )
            self._send_json(
                400,
                {
                    "error": {
                        "type": "acp2_budget_refusal",
                        "code": "unapproved_model",
                        "message": f"model {model!r} is not approved; no fallback is attempted",
                        "batch_id": snapshot["batch_id"],
                        "scenario_budget_id": store.budget_id,
                    }
                },
            )
            return

        estimate = estimate_input_tokens(payload)
        try:
            reservation = self.proxy.store.reserve(
                run_id=run_id,
                model=model,
                stream=stream,
                estimated_input=estimate,
                requested_max_tokens=requested_max,
            )
        except BudgetRefusalError as refusal:
            snapshot = self.proxy.store.snapshot()
            self.proxy.log_event(
                {
                    "kind": "refusal",
                    "code": refusal.code,
                    "model": model,
                    "run_id": run_id,
                    "batch_id": snapshot["batch_id"],
                }
            )
            self._send_json(
                refusal.status,
                refusal.body(snapshot["batch_id"], self.proxy.store.budget_id),
            )
            return
        except (OSError, ValueError) as exc:
            self._send_json(503, {"error": {"type": "acp2_proxy_ledger", "message": str(exc)}})
            return

        self.proxy.log_event(
            {
                "kind": "reserve",
                "rid": reservation.rid,
                "model": model,
                "run_id": run_id,
                "stream": stream,
                "estimated_input_tokens": estimate,
                "requested_max_tokens": requested_max,
                "sent_max_tokens": reservation.sent_max_tokens,
            }
        )
        if stream:
            self._stream_request(reservation, payload)
        else:
            self._buffered_request(reservation, payload)

    # -- upstream helpers --------------------------------------------------

    def _wire_payload(self, payload: dict[str, Any], reservation: Reservation) -> dict[str, Any]:
        body = json.loads(json.dumps(payload))
        if "max_tokens" in body or "max_completion_tokens" not in body:
            body["max_tokens"] = reservation.sent_max_tokens
        else:
            body["max_completion_tokens"] = reservation.sent_max_tokens
        if reservation.stream:
            options = body.get("stream_options")
            if not isinstance(options, dict):
                options = {}
            options["include_usage"] = True
            body["stream_options"] = options
        return body

    def _upstream_connection(self) -> http.client.HTTPConnection:
        upstream = self.proxy.upstream
        host = upstream.hostname or "127.0.0.1"
        port = upstream.port or (443 if upstream.scheme == "https" else 80)
        if upstream.scheme == "https":
            return http.client.HTTPSConnection(host, port, timeout=UPSTREAM_TIMEOUT)
        return http.client.HTTPConnection(host, port, timeout=UPSTREAM_TIMEOUT)

    def _upstream_path(self) -> str:
        base = self.proxy.upstream.path.rstrip("/")
        tail = urllib.parse.urlsplit(self.path).path
        if base.endswith("/v1") and tail.startswith("/v1"):
            tail = tail[3:]
        return (base + tail) or "/"

    def _upstream_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json",
            "User-Agent": "acp2-budget-proxy/1.0",
        }
        for name in ("Authorization", "x-api-key", "x-api-version", "OpenAI-Organization"):
            value = self.headers.get(name)
            if value:
                headers[name] = value
        return headers

    # -- non-streaming -----------------------------------------------------

    def _buffered_request(self, reservation: Reservation, payload: dict[str, Any]) -> None:
        connection: http.client.HTTPConnection | None = None
        try:
            connection = self._upstream_connection()
            body = json.dumps(self._wire_payload(payload, reservation)).encode("utf-8")
            connection.request(
                "POST", self._upstream_path(), body=body, headers=self._upstream_headers()
            )
            response = connection.getresponse()
            raw = response.read()
            status = response.status
            content_type = response.getheader("Content-Type") or "application/json"
        except (http.client.HTTPException, OSError) as exc:
            self.proxy.store.settle(
                reservation,
                outcome="upstream_error",
                usage=None,
                detail=f"{type(exc).__name__}: {exc}",
            )
            self.proxy.log_event(
                {
                    "kind": "settle",
                    "rid": reservation.rid,
                    "outcome": "upstream_error",
                    "detail": f"{type(exc).__name__}: {exc}",
                }
            )
            self._send_json(
                502,
                {
                    "error": {
                        "type": "acp2_proxy_upstream",
                        "message": f"{type(exc).__name__}: {exc}",
                    }
                },
            )
            return
        finally:
            if connection is not None:
                connection.close()

        usage = _usage_from_json_bytes(raw)
        outcome = "completed" if 200 <= status < 300 else f"upstream_status_{status}"
        self.proxy.store.settle(reservation, outcome=outcome, usage=usage, detail=None)
        self.proxy.log_event(
            {
                "kind": "settle",
                "rid": reservation.rid,
                "outcome": outcome,
                "status": status,
                "usage": usage,
            }
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- streaming ---------------------------------------------------------

    def _chunk(self, data: bytes) -> None:
        if not data:
            return
        self.wfile.write(f"{len(data):x}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _stream_error_frame(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document, sort_keys=True).encode("utf-8")
        self._chunk(b"event: error\ndata: " + payload + b"\n\n")

    def _stream_request(self, reservation: Reservation, payload: dict[str, Any]) -> None:
        """Send SSE headers immediately, then stream upstream bytes.

        Sending the headers before the upstream call makes a client disconnect
        observable while the provider is still working.  If the upstream fails
        after the headers, the proxy emits an ``event: error`` frame and ends
        the stream instead of retrying or fabricating content.
        """

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        collector = SseUsageCollector()
        connection: http.client.HTTPConnection | None = None
        settled = False

        def settle(outcome: str, usage: tuple[int | None, int | None] | None, detail: Any) -> None:
            nonlocal settled
            if settled:
                return
            settled = True
            self.proxy.store.settle(reservation, outcome=outcome, usage=usage, detail=detail)
            self.proxy.log_event(
                {
                    "kind": "settle",
                    "rid": reservation.rid,
                    "outcome": outcome,
                    "usage": usage,
                    "detail": detail,
                }
            )

        try:
            connection = self._upstream_connection()
            body = json.dumps(self._wire_payload(payload, reservation)).encode("utf-8")
            connection.request(
                "POST", self._upstream_path(), body=body, headers=self._upstream_headers()
            )
            response = connection.getresponse()
            if response.status != 200:
                raw = response.read()
                settle(
                    f"upstream_status_{response.status}",
                    _usage_from_json_bytes(raw),
                    None,
                )
                self._stream_error_frame(
                    {
                        "type": "acp2_proxy_upstream_status",
                        "status": response.status,
                        "body": raw.decode("utf-8", "replace")[:2000],
                    }
                )
                self._end_chunks()
                return
            while True:
                chunk = response.read(1024)
                if not chunk:
                    break
                collector.feed(chunk)
                self._chunk(chunk)
            if collector.saw_done or collector.usage is not None:
                self._end_chunks()
                settle("completed", collector.usage, None)
            else:
                settle(
                    "upstream_interrupted",
                    collector.usage,
                    "stream ended without [DONE] or usage",
                )
                self._stream_error_frame(
                    {
                        "type": "acp2_proxy_upstream_interrupted",
                        "message": "stream ended without [DONE] or usage",
                    }
                )
                self._end_chunks()
        except (BrokenPipeError, ConnectionResetError) as exc:
            settle("client_disconnected", collector.usage, f"{type(exc).__name__}: {exc}")
        except (http.client.HTTPException, OSError) as exc:
            settle("upstream_interrupted", collector.usage, f"{type(exc).__name__}: {exc}")
            try:
                self._stream_error_frame(
                    {"type": "acp2_proxy_upstream_error", "message": f"{type(exc).__name__}: {exc}"}
                )
                self._end_chunks()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        finally:
            if connection is not None:
                connection.close()
            settle("proxy_interrupted", collector.usage, "unsettled path")


# ---------------------------------------------------------------------------
# CLI: start / ready / stop / health / selftest
# ---------------------------------------------------------------------------


def _loopback_address(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(f"host must be a loopback address: {host}") from exc
    if not address.is_loopback:
        raise ValueError(f"host must be loopback (127.0.0.0/8 or ::1), got {host}")
    return host


def _write_state(path: Path, state: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def _read_state(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema") != STATE_SCHEMA:
        raise ValueError(f"not a proxy state file: {path}")
    return document


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - loopback only
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            document = json.loads(body or b"{}")
        except json.JSONDecodeError:
            document = {"raw": body.decode("utf-8", "replace")}
        return exc.code, document
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def cmd_start(args: argparse.Namespace) -> int:
    try:
        host = _loopback_address(args.host)
        data_root = _data_root(args.data_root)
        caps = _caps_from_args(args)
    except ValueError as exc:
        print(f"budget-proxy: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    try:
        upstream = _normalize_base_url(args.upstream_base_url)
    except ValueError as exc:
        print(f"budget-proxy: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED

    state_path = Path(args.state_file).expanduser()
    if not state_path.is_absolute():
        print(f"budget-proxy: --state-file must be absolute: {state_path}", file=sys.stderr)
        return EXIT_USAGE
    log_path = Path(args.log_file).expanduser() if args.log_file else default_log_path(data_root)

    store = BudgetStore(data_root, args.scenario_budget_id, caps, list(args.model))
    try:
        store.ensure()
    except CapsMismatchError as exc:
        print(f"budget-proxy: caps_mismatch: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    except (OSError, ValueError) as exc:
        print(f"budget-proxy: ledger_error: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    recovered = store.recover_inflight()

    started_at = _now()
    try:
        server = ProxyServer(
            (host, int(args.port)),
            ProxyHandler,
            store=store,
            upstream=upstream,
            model_ids=list(args.model),
            log_path=log_path,
            state_path=state_path,
            started_at=started_at,
        )
    except OSError as exc:
        print(f"budget-proxy: bind_failed: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED

    bound_host, bound_port = server.server_address[:2]
    base_url = f"http://{bound_host}:{bound_port}/v1"
    health_url = f"http://{bound_host}:{bound_port}/health"
    ready_url = f"http://{bound_host}:{bound_port}/ready"
    snapshot = store.snapshot()
    state = {
        "schema": STATE_SCHEMA,
        "pid": os.getpid(),
        "host": bound_host,
        "port": bound_port,
        "base_url": base_url,
        "health_url": health_url,
        "ready_url": ready_url,
        "batch_id": snapshot["batch_id"],
        "scenario_budget_id": args.scenario_budget_id,
        "caps": snapshot["caps"],
        "models": snapshot["models"],
        "ledger_path": snapshot["ledger_path"],
        "log_path": str(log_path),
        "upstream_base_url": args.upstream_base_url,
        "started_at": started_at,
        "recovered_inflight_requests": recovered,
        "stopped_at": None,
    }
    _write_state(state_path, state)
    server.log_event({"kind": "start", "state": {k: v for k, v in state.items() if k != "pid"}})
    print(
        "budget-proxy: "
        f"url={base_url} health={health_url} ready={ready_url} "
        f"batch_id={snapshot['batch_id']} "
        f"scenario_budget_id={args.scenario_budget_id} "
        f"ledger={snapshot['ledger_path']}"
    )
    sys.stdout.flush()

    def _signal(_signum: int, _frame: Any) -> None:
        threading.Thread(target=server.shutdown, daemon=True).start()

    for signal_name in ("SIGTERM", "SIGINT"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            signal.signal(signal_value, _signal)

    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        state["stopped_at"] = _now()
        try:
            _write_state(state_path, state)
        except OSError:
            pass
    return EXIT_PASS


def cmd_ready(args: argparse.Namespace) -> int:
    try:
        state = _read_state(Path(args.state_file).expanduser())
    except (OSError, ValueError) as exc:
        print(f"budget-proxy: not_ready: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    status, document = _http_json(state["ready_url"], timeout=args.timeout)
    if status == 200 and document.get("ready") is True:
        print(
            "budget-proxy: ready "
            f"url={state['base_url']} batch_id={document.get('batch_id')} "
            f"scenario_budget_id={state['scenario_budget_id']}"
        )
        return EXIT_PASS
    print(f"budget-proxy: not_ready: status={status} document={document}", file=sys.stderr)
    return EXIT_NOT_VERIFIED


def cmd_stop(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file).expanduser()
    try:
        state = _read_state(state_path)
    except (OSError, ValueError) as exc:
        print(f"budget-proxy: no_state: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    status, document = _http_json(
        f"http://{state['host']}:{state['port']}/__stop",
        method="POST",
        payload={},
        timeout=args.timeout,
    )
    if state.get("stopped_at"):
        print(f"budget-proxy: already_stopped pid={state['pid']}")
        return EXIT_PASS
    pid = int(state.get("pid") or 0)
    deadline = time.time() + max(2.0, float(args.timeout))
    while time.time() < deadline:
        if pid and not _pid_alive(pid):
            break
        time.sleep(0.1)
    alive = bool(pid) and _pid_alive(pid)
    if alive:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.3)
        alive = _pid_alive(pid)
    print(
        "budget-proxy: stop "
        f"pid={pid} signal_status={status} still_alive={alive} response={document}"
    )
    return EXIT_FAIL if alive else EXIT_PASS


def cmd_health(args: argparse.Namespace) -> int:
    try:
        state = _read_state(Path(args.state_file).expanduser())
    except (OSError, ValueError) as exc:
        print(f"budget-proxy: no_state: {exc}", file=sys.stderr)
        return EXIT_NOT_VERIFIED
    status, document = _http_json(state["health_url"], timeout=args.timeout)
    print(json.dumps(document, indent=2, sort_keys=True))
    return EXIT_PASS if status == 200 else EXIT_NOT_VERIFIED


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state_path = Path(f"/proc/{pid}/status")
        if state_path.is_file():
            for line in state_path.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith("State:"):
                    return "zombie" not in line.lower()
    except OSError:
        pass
    return True


# ---------------------------------------------------------------------------
# Selftest: controlled upstream, full boundary matrix, no real model request
# ---------------------------------------------------------------------------


class _StubServer(ThreadingHTTPServer):
    """Quiet test server: a deliberately-dropped client is a fixture, not an error."""

    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class StubUpstream:
    """Deterministic OpenAI-compatible upstream used only by ``selftest``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        handler = type("_StubHandler", (_StubHandler,), {"stub": self})
        self.server = _StubServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.1}
        )
        self.thread.daemon = True

    def start(self) -> StubUpstream:
        self.thread.start()
        return self

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def record(self, entry: dict[str, Any]) -> int:
        with self._lock:
            self.calls.append(entry)
            return len(self.calls)

    def count(self, model: str | None = None) -> int:
        with self._lock:
            if model is None:
                return len(self.calls)
            return sum(1 for item in self.calls if item.get("model") == model)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"calls": len(self.calls), "entries": list(self.calls)}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    stub: StubUpstream

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        body = json.dumps({"object": "list", "data": [{"id": "stub-ok"}]}).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        model = payload.get("model") or "stub-ok"
        stream = bool(payload.get("stream"))
        options = payload.get("stream_options")
        include_usage = bool(isinstance(options, dict) and options.get("include_usage"))
        self.stub.record(
            {
                "model": model,
                "stream": stream,
                "max_tokens": payload.get("max_tokens", payload.get("max_completion_tokens")),
                "include_usage": include_usage,
                "path": urllib.parse.urlsplit(self.path).path,
            }
        )

        if model == "stub-error":
            body = json.dumps({"error": {"message": "stub upstream failure"}}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if model == "stub-slow" and not stream:
            time.sleep(1.2)

        if not stream:
            out_tokens = 10 if model == "stub-usage10" else 7
            document: dict[str, Any] = {
                "id": "stub-cmpl",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
            }
            if model != "stub-no-usage":
                document["usage"] = {
                    "prompt_tokens": 11,
                    "completion_tokens": out_tokens,
                    "total_tokens": 11 + out_tokens,
                }
            body = json.dumps(document).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def chunk(data: bytes) -> None:
            self.wfile.write(f"{len(data):x}\r\n".encode())
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        deltas = ["a", "b", "c", "d", "e"] if model == "stub-slow" else ["o", "k"]
        delay = 0.15 if model == "stub-slow" else 0.0
        # ``stub-slow`` is also the client-disconnect fixture: after the client
        # drops its socket the proxy must still have data to write, or every
        # write lands in the kernel buffer and the disconnect is invisible.
        padding = "x" * 262144 if model == "stub-slow" else ""
        for piece in deltas:
            event = {
                "id": "stub-cmpl",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {"index": 0, "delta": {"content": piece + padding}, "finish_reason": None}
                ],
            }
            chunk(b"data: " + json.dumps(event).encode() + b"\n\n")
            if delay:
                time.sleep(delay)
        if model == "stub-interrupt":
            # Abruptly close the transport without [DONE] and without usage.
            # ``socket.shutdown`` is required: ``self.connection.close()`` does
            # not close the descriptor while ``rfile``/``wfile`` hold references.
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        if model != "stub-no-usage":
            usage_event = {
                "id": "stub-cmpl",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }
            chunk(b"data: " + json.dumps(usage_event).encode() + b"\n\n")
        chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


class Selftest:
    def __init__(self, out: Path, data_root: Path, dsh_env: Path | None) -> None:
        self.out = out
        self.data_root = data_root
        self.dsh_env = dsh_env
        self.checks: list[dict[str, Any]] = []
        self.stub = StubUpstream().start()
        self.proxy_script = Path(__file__).resolve()
        self.artifacts: list[str] = []

    # -- bookkeeping ------------------------------------------------------

    def check(self, name: str, ok: bool, detail: Any = "") -> bool:
        self.checks.append(
            {
                "name": name,
                "status": "PASS" if ok else "FAIL",
                "detail": (str(detail)[:2000] if detail is not None else None),
            }
        )
        return ok

    def artifact(self, name: str) -> str:
        if name not in self.artifacts:
            self.artifacts.append(name)
        return name

    # -- proxy lifecycle --------------------------------------------------

    def start_proxy(
        self,
        name: str,
        *,
        budget_id: str,
        models: list[str] | None = None,
        caps: dict[str, int] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        data_root: Path | None = None,
    ) -> tuple[dict[str, Any] | None, int, str]:
        root = data_root or self.data_root
        state_file = self.out / f"state-{name}.json"
        log_file = self.out / f"proxy-{name}.jsonl"
        argv = [
            sys.executable,
            str(self.proxy_script),
            "start",
            "--data-root",
            str(root),
            "--scenario-budget-id",
            budget_id,
            "--upstream-base-url",
            self.stub.base_url,
            "--state-file",
            str(state_file),
            "--log-file",
            str(log_file),
            "--host",
            host,
            "--port",
            str(port),
        ]
        for model in models or []:
            argv.extend(["--model", model])
        if caps:
            argv.extend(["--max-requests", str(caps["model_requests"])])
            argv.extend(["--input-token-cap", str(caps["input_tokens"])])
            argv.extend(["--output-token-cap", str(caps["output_tokens"])])
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.time() + 15.0
        while time.time() < deadline:
            if state_file.is_file():
                try:
                    state = json.loads(state_file.read_text(encoding="utf-8"))
                    if state.get("port"):
                        status, document = _http_json(state["ready_url"], timeout=2.0)
                        if status == 200 and document.get("ready"):
                            return state, process, log_file.name
                except (OSError, ValueError, json.JSONDecodeError):
                    pass
            if process.poll() is not None:
                break
            time.sleep(0.05)
        stdout = ""
        stderr = ""
        try:
            stdout, stderr = process.communicate(timeout=2)
        except Exception:  # noqa: BLE001 - diagnostic only
            process.kill()
        return None, process, f"{stdout}{stderr}"

    # -- HTTP helpers -----------------------------------------------------

    @staticmethod
    def chat(
        base_url: str,
        payload: dict[str, Any],
        *,
        run_id: str | None = None,
        timeout: float = 30.0,
    ) -> tuple[int, dict[str, Any]]:
        headers = {}
        if run_id is not None:
            headers["X-ACP2-Run-Id"] = run_id
        return _http_json(
            f"{base_url}/chat/completions",
            method="POST",
            payload=payload,
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    def stream(
        base_url: str,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
        close_after_headers: bool = False,
        byte_budget: int | None = None,
    ) -> tuple[int, bytes, bool]:
        parsed = urllib.parse.urlsplit(base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 80
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        body = json.dumps(payload).encode()
        try:
            connection.request(
                "POST",
                f"{parsed.path.rstrip('/')}/chat/completions",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
        except (http.client.HTTPException, OSError) as exc:
            connection.close()
            return 0, f"{type(exc).__name__}: {exc}".encode(), True
        data = b""
        truncated = False
        if close_after_headers:
            # Read one body byte so the peer is certainly connected to the stream,
            # then drop the socket: the proxy's next chunk write must fail.
            try:
                data = response.read(1)
            except (http.client.HTTPException, OSError):
                truncated = True
            try:
                if connection.sock is not None:
                    connection.sock.close()
            finally:
                connection.close()
            return response.status, data, truncated
        try:
            if byte_budget is not None:
                data = response.read(byte_budget)
            else:
                data = response.read()
        except (http.client.HTTPException, OSError) as exc:
            truncated = True
            data += f"[read-error {type(exc).__name__}]".encode()
        finally:
            connection.close()
        return response.status, data, truncated

    # -- matrix -----------------------------------------------------------

    def run(self) -> dict[str, Any]:
        self.out.mkdir(parents=True, exist_ok=True)
        try:
            self._matrix()
        except Exception as exc:  # noqa: BLE001 - selftest must record, not crash
            self.check("selftest.unhandled_exception", False, f"{type(exc).__name__}: {exc}")
        finally:
            self.stub.close()
            try:
                shutil.copyfile(ledger_path(self.data_root), self.out / "ledger.json")
                self.artifact("ledger.json")
            except OSError as exc:
                self.check("selftest.ledger_copy", False, str(exc))
            snapshot = {
                "schema": SELFTEST_SCHEMA,
                "status": "PASS"
                if all(item["status"] == "PASS" for item in self.checks)
                else "FAIL",
                "started_at": self.started_at,
                "finished_at": _now(),
                "checks": self.checks,
                "artifacts": self.artifacts,
                "data_root": str(self.data_root),
                "ledger_path": str(ledger_path(self.data_root)),
                "stub_upstream": self.stub.snapshot(),
            }
            _atomic_write(
                self.out / "selftest.json",
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            )
        return snapshot

    def _matrix(self) -> None:
        self.started_at = _now()
        models = list(SELFTEST_MODELS)

        # 1. lifecycle: start/ready/health/stop, default ledger location
        state, process, detail = self.start_proxy(
            "lifecycle", budget_id="selftest-lifecycle", models=models
        )
        if state is None:
            self.check("lifecycle.start_ready", False, detail)
            return
        self.check("lifecycle.start_ready", True, state["base_url"])
        self.check(
            "lifecycle.ledger_under_budget_dir",
            Path(state["ledger_path"]).parent == self.data_root / "budget",
            state["ledger_path"],
        )
        status, health = _http_json(state["health_url"])
        self.check(
            "lifecycle.health_caps",
            status == 200
            and health.get("caps") == DEFAULT_CAPS
            and health.get("usage", {}).get("model_calls") == 0,
            json.dumps(health.get("caps")),
        )
        self.check(
            "lifecycle.batch_id_present",
            isinstance(health.get("batch_id"), str) and health["batch_id"].startswith("batch-"),
            health.get("batch_id"),
        )
        self.check(
            "lifecycle.zero_requests_at_start",
            health.get("usage", {}).get("model_calls") == 0,
            health.get("usage"),
        )
        stop_rc = self._stop("lifecycle", state)
        self.check("lifecycle.stop", stop_rc == 0, f"rc={stop_rc}")

        # 2. twelve allowed + thirteenth refused, count is exact
        self._case_count_and_13th(models)
        # 3. per-request output cap boundary
        self._case_per_request_cap(models)
        # 4. streaming usage capture
        self._case_streaming_usage(models)
        # 5. missing usage is unknown, never fabricated
        self._case_missing_usage(models)
        # 6. interruption settlement (upstream abrupt close + client disconnect)
        self._case_interruption(models)
        # 7. upstream error settlement
        self._case_upstream_error(models)
        # 8. restart inherits the remaining allowance
        self._case_restart(models)
        # 9. run_id change never resets the cumulative budget
        self._case_run_id(models)
        # 10. cumulative input cap refusal
        self._case_input_cap(models)
        # 11. cumulative output cap refusal
        self._case_output_cap(models)
        # 12. concurrency 1
        self._case_concurrency(models)
        # 13. unapproved model is refused without fallback
        self._case_unapproved_model(models)
        # 14. crash recovery charges the in-flight request
        self._case_crash_recovery(models)
        # 15. caps cannot be silently changed on restart
        self._case_caps_mismatch(models)
        # 16. Web and headless DSH routes both point at the proxy
        self._case_routing(models)

    def _stop(self, name: str, state: dict[str, Any]) -> int:
        argv = [
            sys.executable,
            str(self.proxy_script),
            "stop",
            "--state-file",
            str(self.out / f"state-{name}.json"),
        ]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=25)
        return result.returncode

    def _case_count_and_13th(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "count", budget_id="selftest-count", models=models
        )
        if state is None:
            self.check("count.start", False, detail)
            return
        before = self.stub.count()
        statuses = []
        for index in range(12):
            status, document = self.chat(
                state["base_url"],
                {
                    "model": "stub-ok",
                    "messages": [{"role": "user", "content": f"ping {index}"}],
                    "max_tokens": 16,
                },
                run_id="run-count-a",
            )
            statuses.append((status, document.get("usage", {}).get("completion_tokens")))
        self.check(
            "count.twelve_allowed",
            all(item[0] == 200 for item in statuses),
            statuses[:4],
        )
        self.check(
            "count.upstream_calls_exactly_twelve",
            self.stub.count() - before == 12,
            f"delta={self.stub.count() - before}",
        )
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "thirteenth"}],
                "max_tokens": 16,
            },
            run_id="run-count-a",
        )
        body = document.get("error", {})
        self.check(
            "count.thirteenth_refused",
            status == 429 and body.get("code") == "model_requests",
            f"status={status} error={body}",
        )
        self.check(
            "count.refused_request_not_forwarded",
            self.stub.count() - before == 12,
            f"delta={self.stub.count() - before}",
        )
        _status, health = _http_json(state["health_url"])
        usage = health.get("usage", {})
        self.check(
            "count.usage_exactly_twelve",
            usage.get("model_calls") == 12
            and usage.get("output_tokens_reported") == 12 * 7
            and usage.get("input_tokens_reported") == 12 * 11,
            usage,
        )
        self.check(
            "count.remaining_zero",
            health.get("remaining", {}).get("model_requests") == 0,
            health.get("remaining"),
        )
        self.check(
            "count.refusal_recorded",
            health.get("refused", {}).get("model_requests") == 1,
            health.get("refused"),
        )
        self._stop("count", state)

    def _case_per_request_cap(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "per-request", budget_id="selftest-per-request", models=models
        )
        if state is None:
            self.check("per_request_cap.start", False, detail)
            return
        before = self.stub.count()
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "too much"}],
                "max_tokens": PER_REQUEST_OUTPUT_CAP + 1,
            },
        )
        self.check(
            "per_request_cap.refused",
            status == 400 and document.get("error", {}).get("code") == "per_request_output_cap",
            f"status={status} error={document.get('error')}",
        )
        self.check(
            "per_request_cap.not_forwarded",
            self.stub.count() == before,
            f"calls={self.stub.count() - before}",
        )
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "at the boundary"}],
                "max_tokens": PER_REQUEST_OUTPUT_CAP,
            },
        )
        self.check(
            "per_request_cap.boundary_allowed",
            status == 200,
            f"status={status}",
        )
        _status, health = _http_json(state["health_url"])
        entries = self.stub.calls
        self.check(
            "per_request_cap.sent_max_tokens_clamped_to_cap",
            health.get("usage", {}).get("model_calls") == 1
            and bool(entries)
            and entries[-1].get("max_tokens") == PER_REQUEST_OUTPUT_CAP,
            {"usage": health.get("usage"), "last": entries[-1] if entries else None},
        )
        self._stop("per-request", state)

    def _case_streaming_usage(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "stream", budget_id="selftest-stream", models=models
        )
        if state is None:
            self.check("streaming.start", False, detail)
            return
        status, data, _truncated = self.stream(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "stream"}],
                "max_tokens": 32,
                "stream": True,
            },
        )
        self.check(
            "streaming.sse_delivered",
            status == 200 and b"data: [DONE]" in data,
            f"status={status} bytes={len(data)}",
        )
        _status, health = _http_json(state["health_url"])
        usage = health.get("usage", {})
        self.check(
            "streaming.usage_reported",
            usage.get("model_calls") == 1
            and usage.get("input_tokens_reported") == 11
            and usage.get("output_tokens_reported") == 7
            and usage.get("unknown_usage_events") == 0,
            usage,
        )
        entries = self.stub.calls
        self.check(
            "streaming.proxy_requested_usage_in_stream",
            bool(entries) and entries[-1].get("include_usage") is True,
            entries[-1] if entries else None,
        )
        self._stop("stream", state)

    def _case_missing_usage(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "no-usage", budget_id="selftest-no-usage", models=models
        )
        if state is None:
            self.check("missing_usage.start", False, detail)
            return
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-no-usage",
                "messages": [{"role": "user", "content": "no usage"}],
                "max_tokens": 16,
            },
        )
        self.check("missing_usage.response_ok", status == 200, f"status={status}")
        _status, health = _http_json(state["health_url"])
        usage = health.get("usage", {})
        self.check(
            "missing_usage.output_recorded_unknown",
            usage.get("model_calls") == 1
            and usage.get("output_tokens_reported") == 0
            and usage.get("output_tokens_unknown_events") == 1
            and usage.get("total_tokens_complete") is False,
            usage,
        )
        self.check(
            "missing_usage.input_marked_estimated",
            usage.get("input_tokens_estimated", 0) > 0 and usage.get("input_tokens_reported") == 0,
            usage,
        )
        _status, health = _http_json(state["health_url"])
        event = (health.get("events_tail") or [{}])[-1]
        self.check(
            "missing_usage.event_sources",
            event.get("usage", {}).get("output_source") == "unknown"
            and event.get("usage", {}).get("output_tokens") is None,
            event.get("usage"),
        )
        self._stop("no-usage", state)

    def _case_interruption(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "interrupt", budget_id="selftest-interrupt", models=models
        )
        if state is None:
            self.check("interruption.start", False, detail)
            return
        status, data, truncated = self.stream(
            state["base_url"],
            {
                "model": "stub-interrupt",
                "messages": [{"role": "user", "content": "cut"}],
                "max_tokens": 16,
                "stream": True,
            },
        )
        self.check(
            "interruption.upstream_close_visible",
            status == 200 and (b"event: error" in data or truncated),
            f"status={status} truncated={truncated} tail={data[-120:]!r}",
        )
        _status, health = _http_json(state["health_url"])
        outcomes = [item.get("outcome") for item in health.get("events_tail", [])]
        self.check(
            "interruption.upstream_settled_as_consumed",
            health.get("usage", {}).get("model_calls") == 1 and "upstream_interrupted" in outcomes,
            outcomes,
        )

        # Client-side disconnect while the provider is still streaming.
        status, _data, _truncated = self.stream(
            state["base_url"],
            {
                "model": "stub-slow",
                "messages": [{"role": "user", "content": "disconnect"}],
                "max_tokens": 16,
                "stream": True,
            },
            close_after_headers=True,
        )
        self.check("interruption.client_headers_received", status == 200, f"status={status}")
        deadline = time.time() + 5.0
        event_outcomes: list[str] = []
        while time.time() < deadline:
            _status, health = _http_json(state["health_url"])
            event_outcomes = [item.get("outcome") for item in health.get("events_tail", [])]
            if "client_disconnected" in event_outcomes:
                break
            time.sleep(0.1)
        self.check(
            "interruption.client_disconnect_settled",
            "client_disconnected" in event_outcomes,
            event_outcomes,
        )
        _status, health = _http_json(state["health_url"])
        self.check(
            "interruption.both_requests_consumed",
            health.get("usage", {}).get("model_calls") == 2,
            health.get("usage"),
        )
        self._stop("interrupt", state)

    def _case_upstream_error(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "upstream-error", budget_id="selftest-upstream-error", models=models
        )
        if state is None:
            self.check("upstream_error.start", False, detail)
            return
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-error",
                "messages": [{"role": "user", "content": "fail"}],
                "max_tokens": 16,
            },
        )
        self.check(
            "upstream_error.status_forwarded",
            status == 500 and "error" in document,
            f"status={status} body={document}",
        )
        _status, health = _http_json(state["health_url"])
        usage = health.get("usage", {})
        outcomes = [item.get("outcome") for item in health.get("events_tail", [])]
        self.check(
            "upstream_error.consumed_with_unknown_usage",
            usage.get("model_calls") == 1
            and usage.get("output_tokens_unknown_events") == 1
            and "upstream_status_500" in outcomes,
            {"usage": usage, "outcomes": outcomes},
        )
        self._stop("upstream-error", state)

    def _case_restart(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "restart-a", budget_id="selftest-restart", models=models
        )
        if state is None:
            self.check("restart.first_start", False, detail)
            return
        batch_id = state["batch_id"]
        for index in range(3):
            self.chat(
                state["base_url"],
                {
                    "model": "stub-ok",
                    "messages": [{"role": "user", "content": f"first {index}"}],
                    "max_tokens": 16,
                },
            )
        first_stop = self._stop("restart-a", state)
        self.check("restart.first_stop", first_stop == 0, f"rc={first_stop}")

        state_b, _process_b, detail_b = self.start_proxy(
            "restart-b", budget_id="selftest-restart", models=models
        )
        if state_b is None:
            self.check("restart.second_start", False, detail_b)
            return
        _status, health = _http_json(state_b["health_url"])
        self.check(
            "restart.inherits_settled_usage",
            health.get("usage", {}).get("model_calls") == 3,
            health.get("usage"),
        )
        self.check(
            "restart.inherits_remaining_requests",
            health.get("remaining", {}).get("model_requests") == 9,
            health.get("remaining"),
        )
        self.check(
            "restart.batch_id_stable",
            health.get("batch_id") == batch_id,
            f"{health.get('batch_id')} == {batch_id}",
        )
        statuses = []
        for index in range(9):
            status, _document = self.chat(
                state_b["base_url"],
                {
                    "model": "stub-ok",
                    "messages": [{"role": "user", "content": f"second {index}"}],
                    "max_tokens": 16,
                },
            )
            statuses.append(status)
        self.check("restart.nine_more_allowed", statuses == [200] * 9, statuses)
        status, document = self.chat(
            state_b["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "thirteenth overall"}],
                "max_tokens": 16,
            },
        )
        self.check(
            "restart.thirteenth_overall_refused",
            status == 429 and document.get("error", {}).get("code") == "model_requests",
            f"status={status} error={document.get('error')}",
        )
        self._stop("restart-b", state_b)

    def _case_run_id(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "run-id", budget_id="selftest-run-id", models=models
        )
        if state is None:
            self.check("run_id.start", False, detail)
            return
        self.chat(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "run a"}],
                "max_tokens": 16,
            },
            run_id="run-alpha",
        )
        for index in range(2):
            self.chat(
                state["base_url"],
                {
                    "model": "stub-ok",
                    "messages": [{"role": "user", "content": f"run b {index}"}],
                    "max_tokens": 16,
                },
                run_id="run-beta",
            )
        _status, health = _http_json(state["health_url"])
        self.check(
            "run_id.change_does_not_reset_budget",
            health.get("usage", {}).get("model_calls") == 3
            and health.get("remaining", {}).get("model_requests") == 9,
            {"usage": health.get("usage"), "remaining": health.get("remaining")},
        )
        self.check(
            "run_id.both_recorded",
            sorted(health.get("run_ids") or []) == ["run-alpha", "run-beta"],
            health.get("run_ids"),
        )
        self._stop("run-id", state)

    def _case_input_cap(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "input-cap",
            budget_id="selftest-input-cap",
            models=models,
            caps={"model_requests": 12, "input_tokens": 50, "output_tokens": 30_000},
        )
        if state is None:
            self.check("input_cap.start", False, detail)
            return
        before = self.stub.count()
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-ok",
                "messages": [{"role": "user", "content": "x" * 400}],
                "max_tokens": 16,
            },
        )
        self.check(
            "input_cap.refused_before_upstream",
            status == 429
            and document.get("error", {}).get("code") == "input_tokens"
            and self.stub.count() == before,
            f"status={status} error={document.get('error')} "
            f"upstream_delta={self.stub.count() - before}",
        )
        self._stop("input-cap", state)

    def _case_output_cap(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "output-cap",
            budget_id="selftest-output-cap",
            models=models,
            caps={"model_requests": 12, "input_tokens": 150_000, "output_tokens": 10},
        )
        if state is None:
            self.check("output_cap.start", False, detail)
            return
        status, _document = self.chat(
            state["base_url"],
            {
                "model": "stub-usage10",
                "messages": [{"role": "user", "content": "one"}],
                "max_tokens": 10,
            },
        )
        self.check("output_cap.first_allowed", status == 200, f"status={status}")
        before = self.stub.count()
        status, document = self.chat(
            state["base_url"],
            {
                "model": "stub-usage10",
                "messages": [{"role": "user", "content": "two"}],
                "max_tokens": 10,
            },
        )
        self.check(
            "output_cap.second_refused",
            status == 429
            and document.get("error", {}).get("code") == "output_tokens"
            and self.stub.count() == before,
            f"status={status} error={document.get('error')}",
        )
        _status, health = _http_json(state["health_url"])
        self.check(
            "output_cap.settled_exactly_at_cap",
            health.get("usage", {}).get("output_tokens_reported") == 10
            and health.get("remaining", {}).get("output_tokens") == 0,
            {"usage": health.get("usage"), "remaining": health.get("remaining")},
        )
        self._stop("output-cap", state)

    def _case_concurrency(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "concurrency", budget_id="selftest-concurrency", models=models
        )
        if state is None:
            self.check("concurrency.start", False, detail)
            return
        results: list[tuple[int, dict[str, Any]]] = []
        barrier = threading.Barrier(2)

        def worker() -> None:
            barrier.wait()
            results.append(
                self.chat(
                    state["base_url"],
                    {
                        "model": "stub-slow",
                        "messages": [{"role": "user", "content": "slow"}],
                        "max_tokens": 16,
                    },
                )
            )

        threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        codes = sorted(item[0] for item in results)
        refusal_codes = [item[1].get("error", {}).get("code") for item in results if item[0] == 429]
        self.check(
            "concurrency.second_refused",
            codes == [200, 429] and refusal_codes == ["concurrency_cap"],
            {"codes": codes, "refusals": refusal_codes},
        )
        self._stop("concurrency", state)

    def _case_unapproved_model(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "unapproved", budget_id="selftest-unapproved", models=models
        )
        if state is None:
            self.check("unapproved.start", False, detail)
            return
        before = self.stub.count()
        status, document = self.chat(
            state["base_url"],
            {
                "model": "not-approved-model",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 16,
            },
        )
        error = document.get("error", {})
        self.check(
            "unapproved.refused_without_fallback",
            status == 400
            and error.get("code") == "unapproved_model"
            and self.stub.count() == before,
            f"status={status} error={error} upstream_delta={self.stub.count() - before}",
        )
        _status, health = _http_json(state["health_url"])
        self.check(
            "unapproved.not_counted_against_budget",
            health.get("usage", {}).get("model_calls") == 0,
            health.get("usage"),
        )
        self._stop("unapproved", state)

    def _case_crash_recovery(self, models: list[str]) -> None:
        budget_id = "selftest-crash"
        store = BudgetStore(self.data_root, budget_id, dict(DEFAULT_CAPS), models)
        store.ensure()
        with _ledger_lock(self.data_root):
            document = _load_ledger(self.data_root)
            scenario = document["scenarios"][budget_id]
            scenario["inflight"] = {"requests": 1, "input_tokens": 321}
            scenario["settled"]["requests"] = 0
            _save_ledger(self.data_root, document)
        state, _process, detail = self.start_proxy("crash", budget_id=budget_id, models=models)
        if state is None:
            self.check("crash.start", False, detail)
            return
        _status, health = _http_json(state["health_url"])
        usage = health.get("usage", {})
        outcomes = [item.get("outcome") for item in health.get("events_tail", [])]
        self.check(
            "crash.inflight_charged_on_restart",
            usage.get("model_calls") == 1
            and usage.get("input_tokens_estimated") == 321
            and usage.get("output_tokens_unknown_events") == 1
            and "restart_recovered" in outcomes,
            {"usage": usage, "outcomes": outcomes},
        )
        self._stop("crash", state)

    def _case_caps_mismatch(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "caps-base", budget_id="selftest-caps", models=models
        )
        if state is None:
            self.check("caps_mismatch.start", False, detail)
            return
        self._stop("caps-base", state)
        _state, process, output = self.start_proxy(
            "caps-change",
            budget_id="selftest-caps",
            models=models,
            caps={"model_requests": 20, "input_tokens": 150_000, "output_tokens": 30_000},
        )
        self.check(
            "caps_mismatch.refused_to_widen",
            _state is None and "caps_mismatch" in output,
            output[-400:],
        )
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                process.kill()

    def _case_routing(self, models: list[str]) -> None:
        state, _process, detail = self.start_proxy(
            "routing", budget_id="selftest-routing", models=models
        )
        if state is None:
            self.check("routing.start", False, detail)
            return
        self.check(
            "routing.proxy_url_recorded",
            state["base_url"].startswith("http://127.0.0.1:"),
            state["base_url"],
        )
        if self.dsh_env is None or not self.dsh_env.is_file():
            self.check(
                "routing.dsh_env_available",
                False,
                f"missing acceptance env: {self.dsh_env}",
            )
            self._stop("routing", state)
            return
        self.check("routing.dsh_env_available", True, str(self.dsh_env))
        overlay = self.out / "routing.cordis.yml"
        overlay.write_text(
            "\n".join(
                [
                    "- id: llm-pi-ai",
                    "  config:",
                    "    providers:",
                    "      acp2-budget-proxy:",
                    "        displayName: ACP2 budget proxy",
                    "        apiKeyEnv: ACP2_PROXY_KEY",
                    "        api: openai-completions",
                    f"        baseURL: {state['base_url']}",
                    "        models:",
                    "          - id: acp2-proxy-model",
                    "            name: ACP2 proxy model",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.artifact("routing.cordis.yml")
        for profile in ("web", "headless"):
            dump_path = self.out / f"routing-{profile}.dump.yml"
            script = 'source "$1" >/dev/null 2>&1; dsh_acceptance "$2" --patch "$3" --dump-config'
            result = subprocess.run(
                ["bash", "-c", script, "_", str(self.dsh_env), profile, str(overlay)],
                capture_output=True,
                text=True,
                timeout=120,
            )
            dump_path.write_text(result.stdout, encoding="utf-8")
            self.artifact(dump_path.name)
            text = result.stdout
            provider_ok = "acp2-budget-proxy" in text and "id: llm-pi-ai" in text
            base_ok = f"baseURL: {state['base_url']}" in text
            api_ok = "api: openai-completions" in text
            secret_ok = SECRET_SCAN_RE.search(text) is None
            self.check(
                f"routing.{profile}_dump_config",
                result.returncode == 0,
                result.stderr.strip()[-400:],
            )
            self.check(
                f"routing.{profile}_points_at_proxy",
                provider_ok and base_ok and api_ok,
                {"provider": provider_ok, "baseURL": base_ok, "api": api_ok},
            )
            self.check(
                f"routing.{profile}_dump_has_no_secret",
                secret_ok,
                "secret-like pattern present" if not secret_ok else "clean",
            )
        routing_summary = {
            "schema": "acp2.model-budget.routing.v1",
            "proxy_base_url": state["base_url"],
            "provider_id": "acp2-budget-proxy",
            "api": "openai-completions",
            "mechanism": (
                "Cordis patch `- id: llm-pi-ai` with "
                "config.providers.<id>.{api,baseURL,models}; Web Settings → Models → "
                "Custom model API writes the same fields to "
                "$DSH_HOME/profiles/web/cordis.patch.yml, headless uses "
                "$DSH_HOME/profiles/headless/cordis.patch.yml"
            ),
            "profiles": {
                profile: {
                    "dump": f"routing-{profile}.dump.yml",
                    "base_url_present": f"baseURL: {state['base_url']}"
                    in (self.out / f"routing-{profile}.dump.yml").read_text(encoding="utf-8"),
                }
                for profile in ("web", "headless")
            },
        }
        _atomic_write(
            self.out / "routing.json",
            json.dumps(routing_summary, indent=2, sort_keys=True) + "\n",
        )
        self.artifact("routing.json")
        self._stop("routing", state)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _add_data_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", help="absolute data root (default: $ACP2_DATA_ROOT)")
    parser.add_argument(
        "--scenario-budget-id",
        default=DEFAULT_SCENARIO_BUDGET_ID,
        help=f"persistent budget identity (default: {DEFAULT_SCENARIO_BUDGET_ID})",
    )
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--input-token-cap", type=int, default=None)
    parser.add_argument("--output-token-cap", type=int, default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acp2_experience_budget_proxy",
        description="ACP 2.0 pre-request model budget proxy (T-13a).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="run the proxy in the foreground (loopback only)")
    _add_data_args(start)
    start.add_argument(
        "--upstream-base-url", required=True, help="OpenAI-compatible upstream base URL"
    )
    start.add_argument("--host", default="127.0.0.1")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--state-file", required=True)
    start.add_argument("--log-file")
    start.add_argument(
        "--model", action="append", default=[], help="approved model id (repeatable)"
    )

    ready = sub.add_parser("ready", help="probe the ready endpoint through the state file")
    ready.add_argument("--state-file", required=True)
    ready.add_argument("--timeout", type=float, default=5.0)

    stop = sub.add_parser("stop", help="stop the proxy recorded in the state file")
    stop.add_argument("--state-file", required=True)
    stop.add_argument("--timeout", type=float, default=10.0)

    health = sub.add_parser("health", help="print the budget health snapshot")
    health.add_argument("--state-file", required=True)
    health.add_argument("--timeout", type=float, default=5.0)

    selftest = sub.add_parser("selftest", help="full boundary matrix against a local stub upstream")
    selftest.add_argument("--out", required=True, help="evidence directory for this selftest")
    selftest.add_argument(
        "--data-root", required=True, help="isolated ledger root for the selftest"
    )
    selftest.add_argument(
        "--dsh-env", default=None, help="acceptance-env-c.sh used for the routing check"
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "start":
        return cmd_start(args)
    if args.command == "ready":
        return cmd_ready(args)
    if args.command == "stop":
        return cmd_stop(args)
    if args.command == "health":
        return cmd_health(args)
    if args.command == "selftest":
        out = Path(args.out).expanduser()
        if not out.is_absolute():
            print("budget-proxy: --out must be absolute", file=sys.stderr)
            return EXIT_USAGE
        try:
            data_root = Path(args.data_root).expanduser()
        except ValueError as exc:
            print(f"budget-proxy: {exc}", file=sys.stderr)
            return EXIT_USAGE
        dsh_env = Path(args.dsh_env).expanduser() if args.dsh_env else None
        selftest = Selftest(out, data_root, dsh_env)
        snapshot = selftest.run()
        status = snapshot["status"]
        passed = sum(1 for item in snapshot["checks"] if item["status"] == "PASS")
        failed = sum(1 for item in snapshot["checks"] if item["status"] == "FAIL")
        print(f"budget-proxy: selftest {status} passed={passed} failed={failed} out={out}")
        if status != "PASS":
            for item in snapshot["checks"]:
                if item["status"] != "PASS":
                    print(f"  FAIL {item['name']}: {item['detail']}", file=sys.stderr)
            return EXIT_FAIL
        return EXIT_PASS
    parser.error(f"unknown command {args.command!r}")
    return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
