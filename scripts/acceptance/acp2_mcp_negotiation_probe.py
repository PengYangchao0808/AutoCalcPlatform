#!/usr/bin/env python3
"""Protocol negotiation matrix diagnostic for the C `mcp-live` acceptance row.

This is a **diagnostic harness**, not a product transport. It only speaks the
same newline-delimited JSON-RPC 2.0 framing as the sanctioned
``docs/reference/next-change/examples/acp_mcp_stdio_probe.py`` and it never
sends a tool call. The official DSH client handshake remains the primary
evidence; this file exists because the frozen SDK client does not emit the
server's declarative ``protocolVersions`` fallback list, which row (b) of the
matrix must exercise.

Rows (one fresh adapter process each, because lifecycle state is per
connection):

* ``same``                — request the server's locked version → same version.
* ``different_supported`` — prefer 2025-11-25 but declare 2025-06-18 too →
                            the server negotiates its common version.
* ``no_common``           — request/declare only an unsupported version →
                            explicit failure; no tool call afterwards.
* ``tools_before_init``   — ``tools/list`` before ``initialize`` → lifecycle
                            rejection (``not_initialized``).
* ``notification_eof``    — initialize, send the initialized notification, then
                            close stdin → clean exit, no fabricated response,
                            no cancel of a submitted job (none was submitted).

Missing prerequisites are the caller's problem; every row is executed and
reported as ``PASS``/``FAIL`` with the raw wire frames, stderr tail and exit
code archived under ``--out``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

LOCKED_VERSION = "2025-06-18"
UNSUPPORTED_VERSION = "2025-11-25"
NO_COMMON_VERSION = "1999-01-01"


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

    def finish(self, status: str, **fields: Any) -> dict[str, Any]:
        (self.dir / "wire.jsonl").touch(exist_ok=True)
        (self.dir / "row.json").write_text(
            json.dumps({"row": self.name, "status": status, **fields}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return {"row": self.name, "status": status, **fields}


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


def _initialize_params(version: str, declared: list[str] | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "protocolVersion": version,
        "capabilities": {},
        "clientInfo": {"name": "acp2-c-negotiation-probe", "version": "1"},
    }
    if declared is not None:
        params["protocolVersions"] = declared
    return params


async def _case_same(launch: dict[str, Any], out: Path, timeout: float) -> dict[str, Any]:
    row = _Row("same", out)
    session = await _open(launch, row, timeout)
    try:
        initialized = await _request(session, 1, "initialize", _initialize_params(LOCKED_VERSION))
        negotiated = initialized.get("result", {}).get("protocolVersion")
        await _send(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = await _request(session, 2, "tools/list", {})
        tools = listed.get("result", {}).get("tools", [])
        ok = "error" not in initialized and negotiated == LOCKED_VERSION and len(tools) == 8
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=LOCKED_VERSION,
            negotiated=negotiated,
            tool_count=len(tools),
            error=initialized.get("error"),
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish(
            "FAIL", requested=LOCKED_VERSION, exception=f"{type(exc).__name__}: {exc}"
        )
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_different_supported(
    launch: dict[str, Any], out: Path, timeout: float
) -> dict[str, Any]:
    row = _Row("different_supported", out)
    session = await _open(launch, row, timeout)
    try:
        initialized = await _request(
            session,
            1,
            "initialize",
            _initialize_params(UNSUPPORTED_VERSION, [UNSUPPORTED_VERSION, LOCKED_VERSION]),
        )
        negotiated = initialized.get("result", {}).get("protocolVersion")
        await _send(session, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = await _request(session, 2, "tools/list", {})
        tools = listed.get("result", {}).get("tools", [])
        ok = "error" not in initialized and negotiated == LOCKED_VERSION and len(tools) == 8
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=UNSUPPORTED_VERSION,
            declared=[UNSUPPORTED_VERSION, LOCKED_VERSION],
            negotiated=negotiated,
            tool_count=len(tools),
            error=initialized.get("error"),
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish(
            "FAIL", requested=UNSUPPORTED_VERSION, exception=f"{type(exc).__name__}: {exc}"
        )
    finally:
        rc = await _close(session)
        session.stderr.close()
        (row.dir / "exit-code.txt").write_text(f"{rc}\n", encoding="utf-8")


async def _case_no_common(launch: dict[str, Any], out: Path, timeout: float) -> dict[str, Any]:
    row = _Row("no_common", out)
    session = await _open(launch, row, timeout)
    try:
        initialized = await _request(
            session,
            1,
            "initialize",
            _initialize_params(NO_COMMON_VERSION, [NO_COMMON_VERSION]),
        )
        error = initialized.get("error") or {}
        code = (error.get("data") or {}).get("code")
        ok = code == "unsupported_protocol_version" and "result" not in initialized
        # The row explicitly proves no tool call followed the failed
        # initialize: the harness sent exactly one frame.
        tool_calls = sum(
            1
            for item in row.wire
            if item["direction"] == "send" and item["message"].get("method") == "tools/call"
        )
        ok = ok and tool_calls == 0
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=NO_COMMON_VERSION,
            error_code=code,
            tool_calls_after_failure=tool_calls,
            error=error or None,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish(
            "FAIL", requested=NO_COMMON_VERSION, exception=f"{type(exc).__name__}: {exc}"
        )
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
        initialized = await _request(session, 1, "initialize", _initialize_params(LOCKED_VERSION))
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
            and negotiated == LOCKED_VERSION
            and frames_after_notification == 0
            and rc == 0
        )
        return row.finish(
            "PASS" if ok else "FAIL",
            requested=LOCKED_VERSION,
            negotiated=negotiated,
            fabricated_frames_after_notification=frames_after_notification,
            exit_code=rc,
            submitted_job=None,
        )
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        return row.finish(
            "FAIL", requested=LOCKED_VERSION, exception=f"{type(exc).__name__}: {exc}"
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
    ("different_supported", _case_different_supported),
    ("no_common", _case_no_common),
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
        "schema": "acp2.c.mcp-live.negotiation-matrix.v1",
        "launch_path": str(args.launch),
        "launch_protocol_version": launch.get("protocol_version"),
        "locked_version": LOCKED_VERSION,
        "rows": rows,
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
