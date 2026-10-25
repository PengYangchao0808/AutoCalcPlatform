#!/usr/bin/env python3.11
"""B15 evidence probe: real stdio MCP launch bound to a server-resolved key.

The probe is acceptance tooling.  It starts the real ``acp mcp serve`` process
with ``ACP_API_KEY`` in its environment, reads the Controller-resolved identity
from ``initialize``, and proves a read-only key cannot call a write tool even
when ``_meta`` forges an admin role and another project.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any


def _read_lines(stream: Any, sink: "queue.Queue[str]") -> None:
    for raw in stream:
        sink.put(raw.decode("utf-8", "replace").rstrip("\n"))


def _request(process: subprocess.Popen[bytes], sink: "queue.Queue[str]", payload: dict[str, Any]) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    process.stdin.flush()
    while True:
        line = sink.get(timeout=30)
        if not line:
            continue
        try:
            response = json.loads(line)
        except json.JSONDecodeError:
            continue
        if response.get("id") == payload.get("id"):
            return response
        raise RuntimeError(f"unexpected frame: {line}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", required=True)
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--key-name", default="bob_read")
    parser.add_argument("--expected-principal", required=True)
    parser.add_argument("--expected-project", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    credentials = json.loads(Path(args.credentials).read_text(encoding="utf-8"))
    key = credentials["keys"][args.key_name]["key"]
    env = dict(os.environ)
    env.update(
        {
            "ACP_API_KEY": key,
            "ACP_CONTROLLER_URL": args.controller,
            "PYTHONPATH": "/opt/acp2-dev/data/b/b15-src",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "acp.cli",
            "mcp",
            "serve",
            "--transport",
            "stdio",
            "--controller",
            args.controller,
            "--log-level",
            "INFO",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    sink: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=_read_lines, args=(process.stdout, sink), daemon=True).start()
    errors: list[str] = []
    assertions: list[dict[str, Any]] = []

    initialize = _request(
        process,
        sink,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "clientInfo": {"name": "b15-stdio-probe", "version": "1.0.0"},
            },
        },
    )
    identity = (
        initialize.get("result", {}).get("extensions", {}).get("acp.identity.v1", {})
    )
    assertions.append(
        {
            "name": "stdio.identity.bound",
            "status": "PASS"
            if identity.get("binding") == "server-resolved-principal"
            and identity.get("principal_id") == args.expected_principal
            and identity.get("project_id") == args.expected_project
            else "FAIL",
            "detail": identity,
        }
    )
    assertions.append(
        {
            "name": "stdio.identity.ceiling",
            "status": "PASS"
            if identity.get("available_actions") == ["read"]
            else "FAIL",
            "detail": identity.get("available_actions"),
        }
    )

    capabilities = _request(
        process,
        sink,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "acp_capabilities", "arguments": {}},
        },
    )
    cap_result = capabilities.get("result", {})
    assertions.append(
        {
            "name": "stdio.read_tool_allowed",
            "status": "PASS" if cap_result.get("isError") is False else "FAIL",
            "detail": cap_result.get("structuredContent", {}).get("status"),
        }
    )

    submit = _request(
        process,
        sink,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "acp_submit",
                "arguments": {"workflow": "fake", "input": {"source": "C"}},
                "_meta": {
                    "roles": ["admin"],
                    "tenant_id": "forged-tenant",
                    "project_id": args.expected_project,
                },
            },
        },
    )
    submit_result = submit.get("result", {})
    error = submit_result.get("structuredContent", {}).get("error") or {}
    assertions.append(
        {
            "name": "stdio.write_tool_denied",
            "status": "PASS"
            if submit_result.get("isError") is True and error.get("code") == "access_denied"
            else "FAIL",
            "detail": error,
        }
    )

    process.stdin.close()  # type: ignore[union-attr]
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        errors.append("adapter did not stop after stdin EOF")
    stderr = process.stderr.read().decode("utf-8", "replace")  # type: ignore[union-attr]
    assertions.append(
        {
            "name": "stdio.eof_stops_adapter",
            "status": "PASS" if process.returncode is not None else "FAIL",
            "detail": {"returncode": process.returncode},
        }
    )

    failed = [item["name"] for item in assertions if item["status"] != "PASS"]
    document = {
        "schema": "acp2.b15.stdio-identity.v1",
        "controller": args.controller,
        "key_name": args.key_name,
        "key_id": credentials["keys"][args.key_name]["key_id"],
        "assertions": assertions,
        "result": "PASS" if not failed else "FAIL",
        "errors": errors,
        "adapter_stderr_tail": stderr[-2000:].splitlines()[-12:],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
