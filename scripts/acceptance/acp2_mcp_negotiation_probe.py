#!/usr/bin/env python3
"""Standard-handshake negotiation matrix for the C `mcp-live` acceptance row.

This is a **diagnostic harness**, not a product transport. It only speaks the
same newline-delimited JSON-RPC 2.0 framing as the sanctioned
``docs/reference/next-change/examples/acp_mcp_stdio_probe.py`` and it never
sends a tool call. The official DSH/SDK client handshake remains the primary
evidence; this file exercises the wire shapes the frozen SDK client emits and
the refusal paths around them.

Post-M0-08 semantics (ADR-006) — the server always answers with a version it
speaks and the client decides compatibility; only a malformed
``protocolVersion``/``protocolVersions`` shape is ``-32602``:

* ``same``                 — request the server's version → same version.
* ``unsupported_version``  — request ``2025-11-25`` with no private fallback
                             list (exactly what the frozen SDK sends) → the
                             server answers ``2025-06-18``; tools list is 8.
* ``private_versions``     — request an unknown version *and* declare it in the
                             private ``protocolVersions`` list → still the
                             server version; the list is recorded, never gated.
* ``malformed_version``    — non-string ``protocolVersion`` → ``-32602``
                             ``invalid_protocol_version``; no tool call.
* ``server_discover``      — an era-probing client sends ``server/discover``
                             before ``initialize`` → ordinary ``-32601``
                             unknown-method (no discover result is faked);
                             ``initialize`` then succeeds in the same process.
* ``tools_before_init``    — ``tools/list`` before ``initialize`` → lifecycle
                             rejection (``not_initialized``).
* ``notification_eof``     — initialize, send the initialized notification, then
                             close stdin → clean exit, no fabricated response,
                             no cancel of a submitted job (none was submitted).

Missing prerequisites are the caller's problem; every row is executed and
reported as ``PASS``/``FAIL`` with the raw wire frames, stderr tail and exit
code archived under ``--out``. Every row also asserts that it sent zero
``tools/call`` frames, so a handshake row can never create a task.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

SERVER_VERSION = "2025-06-18"
SDK_LATEST_VERSION = "2025-11-25"
UNKNOWN_VERSION = "1999-01-01"
MALFORMED_VERSION = 20250618


class _Row:
    def __init__(self, name: str, out: Path) -> None:
        self.name = name
        self.dir = out / name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.wire: list[dict[str, Any]] = []
        self.frames: list[dict[str, Any]] = []

    def record(self, direction: str, message: Any) -> None:
        entry = {"direction": direction, "message": message}
        self.wire.append(entry)
        with (self.dir / "wire.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def tool_calls(self) -> int:
        return sum(
            1
            for item in self.wire
            if item["direction"] == "send" and item["message"].get("method") == "tools/call"
        )

    def finish(self, status: str, **fields: Any) -> dict[str, Any]:
        (self.dir / "wire.jsonl").touch(exist_ok=True)
        document = {
            "row": self.name,
            "status": status,
            "tools_call_frames": self.tool_calls(),
            **fields,
        }
        (self.dir / "row.json").write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return document


class _Session:
    """One adapter process plus its row recorder and captured stderr handle."""

    def __init__(self, process: Any, row: _Row, stderr: Any, timeout: float) -> None:
        self.process = process
        self.row = row
        self.stderr = stderr
        self.timeout = timeout


async def _open(launch: dict[str, Any], row: _Row, timeout: float) -> _Session:
    stderr = (row.dir / "stderr.log").open("w", encoding="utf-8")
    process = await asyncio.create_subprocess_exec(
        launch["command"],
        *launch["args"],
        cwd=launch["cwd"],
        env={**os.environ, **launch["env"]},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        limit=4 * 1024 * 1024,
    )
    return _Session(process, row, stderr, timeout)


async def _send(session: _Session, message: dict[str, Any]) -> None:
    session.row.record("send", message)
    session.process.stdin.write((json.dumps(message) + "\n").encode())
    await session.process.stdin.drain()


async def _receive(session: _Session) -> dict[str, Any] | None:
    line = await asyncio.wait_for(session.process.stdout.readline(), session.timeout)
    if not line:
        return None
    raw = line.decode("utf-8", errors="replace")
    session.row.record("receive_raw", raw)
    message = json.loads(raw)
    session.row.record("receive", message)
    return message


async def _request(
    session: _Session, number: int, method: str, params: dict[str, Any]
) -> dict[str, Any]:
    await _send(session, {"jsonrpc": "2.0", "id": number, "method": method, "params": params})
    while True:
        message = await _receive(session)
        if message is None:
            raise RuntimeError(f"server closed stdout during {method}")
        if message.get("jsonrpc") != "2.0":
            raise RuntimeError("not a JSON-RPC 2.0 object")
        if "id" not in message and "method" in message:
            continue
        return message


async def _close(session: _Session) -> int:
    process = session.process
    try:
        process.stdin.close()
    except Exception:  # noqa: BLE001 - already-closed stdin is not a row failure
        pass
    try:
        return await asyncio.wait_for(process.wait(), 10)
    except TimeoutError:
        process.terminate()
        try:
            return await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            return await process.wait()


def _initialize_params(version: Any, declared: list[str] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "protocolVersion": version,
        "capabilities": {},
        "clientInfo": {"name": "acp2-c-negotiation-probe", "version": "1"},
    }
    if declared is not None:
        params["protocolVersions"] = declared
    return params


def _guidance(initialized: dict[str, Any]) -> dict[str, Any]:
    capabilities = initialized.get("result", {}).get("capabilities") or {}
    extensions = capabilities.get("extensions") or {}
    return extensions.get("acp.negotiation.v1") or {}


def _handshake_blockers(initialized: dict[str, Any]) -> list[str]:
    """Return the M0-08 result-shape violations (empty == conforming)."""
    result = initialized.get("result")
    if not isinstance(result, dict):
        return ["no result"]
    problems: list[str] = []
    if "extensions" in result:
        problems.append("top_level_extensions_present")
    if '"negotiated"' in json.dumps(result.get("capabilities") or {}):
        problems.append("negotiated_claim_present")
    if _guidance(initialized).get("supported_versions") is None:
        problems.append("no_negotiation_guidance")
    return problems


async def _handshake_row(
    name: str,
    launch: dict[str, Any],
    out: Path,
    timeout: float,
    params_factory: Any,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = _Row(name, out)
    session = await _open(launch, row, timeout)
    try:
        initialized = await _request(session, 1, "initialize", params_factory())
        negotiated = initialized.get("result", {}).get("protocolVersion")
        await _send(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = await _request(session, 2, "tools/list", {})
        tools = listed.get("result", {}).get("tools", [])
        problems = _handshake_blockers(initialized)
        ok = (
            "error" not in initialized
            and negotiated == SERVER_VERSION
            and len(tools) == 8
            and not problems
            and row.tool_calls() == 0
        )
        return row.finish(
            "PASS" if ok else "FAIL",
            negotiated=negotiated,
            tool_count=len(tools),
            guidance=_guidance(initialized),
            result_shape_problems=problems,
            error=initialized.get("error"),
            **(extra or {}),
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish("FAIL", exception=f"{type(exc).__name__}: {exc}", **(extra or {}))
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_same(launch: dict[str, Any], out: Path, timeout: float) -> dict[str, Any]:
    return await _handshake_row(
        "same",
        launch,
        out,
        timeout,
        lambda: _initialize_params(SERVER_VERSION),
        extra={"requested": SERVER_VERSION},
    )


async def _case_unsupported_version(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    """Exactly the frozen SDK 2.2.0 request: newest version, no private list."""
    return await _handshake_row(
        "unsupported_version",
        launch,
        out,
        timeout,
        lambda: _initialize_params(SDK_LATEST_VERSION),
        extra={"requested": SDK_LATEST_VERSION, "declared": None},
    )


async def _case_private_versions(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    """The private list is recorded, never a gate."""
    return await _handshake_row(
        "private_versions",
        launch,
        out,
        timeout,
        lambda: _initialize_params(UNKNOWN_VERSION, [UNKNOWN_VERSION]),
        extra={"requested": UNKNOWN_VERSION, "declared": [UNKNOWN_VERSION]},
    )


async def _case_malformed_version(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    row = _Row("malformed_version", out)
    session = await _open(launch, row, timeout)
    try:
        initialized = await _request(
            session, 1, "initialize", _initialize_params(MALFORMED_VERSION)
        )
        error = initialized.get("error") or {}
        code = (error.get("data") or {}).get("code")
        ok = (
            error.get("code") == -32602
            and code == "invalid_protocol_version"
            and "result" not in initialized
            and row.tool_calls() == 0
        )
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=MALFORMED_VERSION,
            error_code=code,
            jsonrpc_code=error.get("code"),
            error=error or None,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish("FAIL", exception=f"{type(exc).__name__}: {exc}")
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_server_discover(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    """Era probe first (ordinary -32601), then the formal initialize."""
    row = _Row("server_discover", out)
    session = await _open(launch, row, timeout)
    try:
        probe = await _request(
            session,
            1,
            "server/discover",
            {
                "protocolVersions": [SDK_LATEST_VERSION],
                "clientInfo": {"name": "acp2-c-era-probe", "version": "1"},
            },
        )
        probe_error = probe.get("error") or {}
        probe_data = probe_error.get("data") or {}
        probe_ok = (
            probe_error.get("code") == -32601
            and probe_data.get("code") != "not_initialized"
            and "result" not in probe
        )
        initialized = await _request(
            session, 2, "initialize", _initialize_params(SDK_LATEST_VERSION)
        )
        negotiated = initialized.get("result", {}).get("protocolVersion")
        await _send(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = await _request(session, 3, "tools/list", {})
        tools = listed.get("result", {}).get("tools", [])
        ok = (
            probe_ok
            and "error" not in initialized
            and negotiated == SERVER_VERSION
            and len(tools) == 8
            and not _handshake_blockers(initialized)
            and row.tool_calls() == 0
        )
        return row.finish(
            "PASS" if ok else "FAIL",
            discover_jsonrpc_code=probe_error.get("code"),
            discover_error_code=probe_data.get("code"),
            discover_result_present="result" in probe,
            negotiated=negotiated,
            tool_count=len(tools),
            result_shape_problems=_handshake_blockers(initialized),
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish("FAIL", exception=f"{type(exc).__name__}: {exc}")
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_tools_before_init(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    row = _Row("tools_before_init", out)
    session = await _open(launch, row, timeout)
    try:
        response = await _request(session, 1, "tools/list", {})
        error = response.get("error") or {}
        code = (error.get("data") or {}).get("code")
        ok = code == "not_initialized" and "result" not in response
        return row.finish("PASS" if ok else "FAIL", error_code=code, error=error or None)
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish("FAIL", exception=f"{type(exc).__name__}: {exc}")
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_notification_eof(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    row = _Row("notification_eof", out)
    session = await _open(launch, row, timeout)
    process = session.process
    try:
        initialized = await _request(session, 1, "initialize", _initialize_params(SERVER_VERSION))
        negotiated = initialized.get("result", {}).get("protocolVersion")
        await _send(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        frames_after_notification = 0
        process.stdin.close()
        while True:
            try:
                message = await _receive(session)
            except TimeoutError:
                break
            if message is None:
                break
            frames_after_notification += 1
        rc = await asyncio.wait_for(process.wait(), 10)
        ok = (
            "error" not in initialized
            and negotiated == SERVER_VERSION
            and frames_after_notification == 0
            and rc == 0
            and row.tool_calls() == 0
        )
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=SERVER_VERSION,
            negotiated=negotiated,
            fabricated_frames_after_notification=frames_after_notification,
            exit_code=rc,
            submitted_job=None,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish(
            "FAIL", requested=SERVER_VERSION, exception=f"{type(exc).__name__}: {exc}"
        )
    finally:
        if process.returncode is None:
            rc = await _close(session)
        else:
            rc = process.returncode
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


CASES = (
    ("same", _case_same),
    ("unsupported_version", _case_unsupported_version),
    ("private_versions", _case_private_versions),
    ("malformed_version", _case_malformed_version),
    ("server_discover", _case_server_discover),
    ("tools_before_init", _case_tools_before_init),
    ("notification_eof", _case_notification_eof),
)


async def run(args: argparse.Namespace) -> int:
    launch = json.loads(args.launch.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for name, handler in CASES:
        rows.append(await handler(launch, args.out, args.timeout))
    document = {
        "schema": "acp2.c.mcp-live.negotiation-matrix.v2",
        "semantics": "mcp-2025-06-18 standard handshake (ADR-006): server answers "
        "its own version, client decides compatibility, private protocolVersions "
        "recorded only; malformed shapes are -32602",
        "launch_path": str(args.launch),
        "launch_protocol_version": launch.get("protocol_version"),
        "server_version": SERVER_VERSION,
        "rows": rows,
        "tools_call_frames": sum(row.get("tools_call_frames", 0) for row in rows),
        "passed": all(row["status"] == "PASS" for row in rows),
    }
    (args.out / "negotiation-matrix.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({row["row"]: row["status"] for row in rows}))
    return 0 if document["passed"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
